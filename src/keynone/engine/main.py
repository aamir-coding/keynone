"""Engine process entry point (spawned on demand by the listener).

Reads framed commands/audio from stdin, writes JSON events to stdout. Models
load in the background the moment the process starts; the first session's
audio is buffered meanwhile, so nothing the user says is lost.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time

from keynone.config import AppProfile, load_config
from keynone.editions import without_missing_language_pack
from keynone.engine.history import History
from keynone.engine.inject import Inserter
from keynone.engine.learn_watch import CorrectionWatcher
from keynone.engine.models import EngineModels
from keynone.engine.overlay import Overlay
from keynone.engine.session import Session, SessionMeta, SessionOutcome
from keynone.ipc import EventWriter, Msg, ProtocolError, read_frame
from keynone.learning import LearnedStore, learns_in
from keynone.logging_setup import setup_logging
from keynone.paths import get_paths
from keynone.win32 import api
from keynone.win32.process import KillOnCloseJob
from keynone.win32.uia import UiaClient

log = logging.getLogger(__name__)


def run_engine(*, verbose: bool = False, overlay_enabled: bool | None = None) -> int:
    paths = get_paths()
    paths.ensure()
    setup_logging("engine", verbose=verbose, paths=paths)
    started = time.perf_counter()
    config, edition_notes = without_missing_language_pack(load_config(paths), paths)
    for note in edition_notes:
        log.warning(note)
    api.enable_dpi_awareness()
    if sys.stdin is None or sys.stdout is None:
        log.error("engine needs stdin/stdout pipes (it is started by `keynone listen`)")
        return 2
    stdin, events = sys.stdin.buffer, EventWriter(sys.stdout.buffer)

    job = KillOnCloseJob()
    models = EngineModels(config, paths, job)
    models.errors.extend(edition_notes)  # shown in the tray once the models are up
    models.start()
    show_overlay = config.overlay.enabled if overlay_enabled is None else overlay_enabled
    overlay = Overlay(opacity=config.overlay.opacity, position=config.overlay.position,
                      enabled=show_overlay)
    overlay.start()
    history = History(paths.history_db, enabled=config.history.enabled,
                      retention_days=config.history.retention_days)
    inserter = Inserter(config.insert)
    uia: UiaClient | None = None
    if config.insert.smart_spacing or config.hotkeys.edit or config.dictionary.learn:
        uia = UiaClient()
        uia.warm_up()  # first use builds COM wrappers (~1 s): do it while models load
    learned = LearnedStore(paths.learned_db)
    watcher: CorrectionWatcher | None = None
    if uia is not None and config.dictionary.learn:
        watcher = CorrectionWatcher(uia, learned, on_learned=lambda items: events.send(
            "learned", items=[{"heard": i.heard, "written": i.written} for i in items]))

    def inserted(text: str, profile: AppProfile) -> None:
        if watcher is not None and learns_in(profile):
            watcher.watch(text)  # the next minute: did the user fix a name?

    events.send("hello", pid=os.getpid())
    threading.Thread(target=_report_ready, args=(models, events, started), daemon=True).start()

    current: Session | None = None

    def finished(outcome: SessionOutcome) -> None:
        fields = {k: v for k, v in vars(outcome).items() if k not in ("text", "raw_text")}
        if config.privacy.log_transcripts:
            log.info("session %s -> %r", outcome.sid, outcome.text)
        log.info("session done: %s", fields)
        events.send("session_done", **fields)

    try:
        while True:
            try:
                frame = read_frame(stdin)
            except ProtocolError:
                log.exception("bad frame from listener")
                break
            if frame is None:  # listener closed the pipe
                break
            kind, payload = frame
            if kind is Msg.AUDIO:
                if current is not None:
                    current.feed(payload)
            elif kind is Msg.START:
                if watcher is not None:
                    watcher.stop()  # the text box changes for other reasons from now on
                meta = SessionMeta(**json.loads(payload))
                current = Session(meta, config=config, models=models, overlay=overlay,
                                  inserter=inserter, history=history, on_finished=finished,
                                  on_event=events.send, uia=uia,
                                  learned=learned.entries() if config.dictionary.learn else (),
                                  on_inserted=inserted)
                current.start()
            elif kind is Msg.STOP:
                if current is not None:
                    current.stop()
            elif kind is Msg.CANCEL:
                if current is not None:
                    current.cancel()
            elif kind is Msg.MODE:
                if current is not None:
                    current.set_mode(json.loads(payload).get("mode", "ptt"))
            elif kind is Msg.QUIT:
                break
    finally:
        if current is not None and not current.done.is_set():
            current.cancel()
            current.done.wait(5)
        if watcher is not None:
            watcher.stop()
        models.close()
        overlay.close()
        history.close()
        learned.close()
        if uia is not None:
            uia.close()
        job.close()
        log.info("engine exiting")
    return 0


def _report_ready(models: EngineModels, events: EventWriter, started: float) -> None:
    models.asr_ready.wait()
    events.send("asr_ready", seconds=round(time.perf_counter() - started, 2),
                ok=models.recognizer is not None)
    models.llm_ready.wait()
    events.send("llm_ready", seconds=round(time.perf_counter() - started, 2),
                ok=models.polisher is not None)
    for error in models.errors:
        events.send("warning", message=error)
