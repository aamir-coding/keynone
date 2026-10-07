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

from keynone.config import AppProfile, Config, ConfigError, config_mtime, load_config
from keynone.editions import without_missing_language_pack
from keynone.engine.history import History
from keynone.engine.inject import Inserter
from keynone.engine.learn_watch import CorrectionWatcher
from keynone.engine.models import EngineModels
from keynone.engine.overlay import Overlay
from keynone.engine.session import Session, SessionMeta, SessionOutcome
from keynone.ipc import EventWriter, Msg, ProtocolError, read_frame
from keynone.learning import Learned, LearnedStore, learns_in
from keynone.logging_setup import setup_logging
from keynone.paths import Paths, get_paths
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
    live = _Live(config, paths, events, overlay_enabled)
    events.send("hello", pid=os.getpid())
    threading.Thread(target=_report_ready, args=(models, events, started), daemon=True).start()

    current: Session | None = None

    def finished(outcome: SessionOutcome) -> None:
        fields = {k: v for k, v in vars(outcome).items() if k not in ("text", "raw_text")}
        if live.config.privacy.log_transcripts:
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
                config = live.refresh()  # settings saved since the last dictation
                meta = SessionMeta(**json.loads(payload))
                current = Session(meta, config=config, models=models, overlay=live.overlay,
                                  inserter=live.inserter, history=live.history,
                                  on_finished=finished, on_event=events.send, uia=live.uia,
                                  learned=live.learned_spellings(),
                                  on_inserted=live.inserted)
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
        models.close()
        live.close()
        job.close()
        log.info("engine exiting")
    return 0


class _Live:
    """What the engine takes from config.toml for each dictation, so saving a setting
    doesn't restart it: the overlay, the history, insertion, UI Automation and learning.
    (The listener restarts the engine for the models: ``engine_restart_needed``.)"""

    def __init__(self, config: Config, paths: Paths, events: EventWriter,
                 overlay_enabled: bool | None) -> None:
        self.paths, self.config, self._events = paths, config, events
        self._overlay_forced = overlay_enabled  # `keynone engine --no-overlay`
        self._stamp = config_mtime(paths)
        self.overlay = Overlay(opacity=config.overlay.opacity, position=config.overlay.position,
                               enabled=self._overlay_on(config))
        self.overlay.start()
        self.history = History(paths.history_db, enabled=config.history.enabled,
                               retention_days=config.history.retention_days)
        self.inserter = Inserter(config.insert)
        self.uia: UiaClient | None = None
        self.learned = LearnedStore(paths.learned_db)
        self.watcher: CorrectionWatcher | None = None
        self._apply(config)

    def _overlay_on(self, config: Config) -> bool:
        return config.overlay.enabled if self._overlay_forced is None else self._overlay_forced

    def refresh(self) -> Config:
        """config.toml again, if it changed (a file with an error keeps the old one)."""
        stamp = config_mtime(self.paths)
        if stamp != self._stamp:
            self._stamp = stamp
            try:
                config, _ = without_missing_language_pack(load_config(self.paths), self.paths)
            except (ConfigError, ValueError) as exc:
                log.warning("config.toml can't be used (%s); keeping the settings in use", exc)
            else:
                old, self.config = self.config, config
                self.overlay.configure(enabled=self._overlay_on(config),
                                       opacity=config.overlay.opacity,
                                       position=config.overlay.position)
                if (old.history.enabled, old.history.retention_days) != (
                        config.history.enabled, config.history.retention_days):
                    self.history.close()
                    self.history = History(self.paths.history_db, enabled=config.history.enabled,
                                           retention_days=config.history.retention_days)
                self.inserter.config = config.insert
                self._apply(config)
                log.info("settings reloaded for this dictation")
        return self.config

    def _apply(self, config: Config) -> None:
        if (config.insert.smart_spacing or config.hotkeys.edit or config.dictionary.learn) \
                and self.uia is None:
            self.uia = UiaClient()
            self.uia.warm_up()  # first use builds COM wrappers (~1 s)
        if config.dictionary.learn and self.uia is not None and self.watcher is None:
            self.watcher = CorrectionWatcher(self.uia, self.learned, self._report_learned)
        if self.watcher is not None:
            self.watcher.stop()  # a new dictation: the text box changes for other reasons now

    def _report_learned(self, items: list[Learned]) -> None:
        self._events.send("learned", items=[{"heard": i.heard, "written": i.written}
                                            for i in items])

    def learned_spellings(self) -> list[Learned]:
        if self.watcher is not None:
            self.watcher.stop()  # this dictation's text box is about to change
        return self.learned.entries() if self.config.dictionary.learn else []

    def inserted(self, text: str, profile: AppProfile) -> None:
        if self.watcher is not None and self.config.dictionary.learn and learns_in(profile):
            self.watcher.watch(text)  # the next minute: did the user fix a name?

    def close(self) -> None:
        if self.watcher is not None:
            self.watcher.stop()
        self.overlay.close()
        self.history.close()
        self.learned.close()
        if self.uia is not None:
            self.uia.close()


def _report_ready(models: EngineModels, events: EventWriter, started: float) -> None:
    models.asr_ready.wait()
    events.send("asr_ready", seconds=round(time.perf_counter() - started, 2),
                ok=models.recognizer is not None)
    models.llm_ready.wait()
    events.send("llm_ready", seconds=round(time.perf_counter() - started, 2),
                ok=models.polisher is not None)
    for error in models.errors:
        events.send("warning", message=error)
