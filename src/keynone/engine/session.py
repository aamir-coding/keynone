"""One dictation, from the first audio frame to inserted text."""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from keynone.cleanup import formatting
from keynone.cleanup.pipeline import CleanupPipeline, CleanupSettings
from keynone.cleanup.rules import RuleOptions
from keynone.cleanup.segmenter import SentenceSegmenter
from keynone.config import Config
from keynone.engine.history import History
from keynone.engine.inject import Inserter
from keynone.engine.models import EngineModels
from keynone.engine.overlay import Overlay
from keynone.win32.windows import WindowInfo, window_info

log = logging.getLogger(__name__)

_STOP = "stop"
_CANCEL = "cancel"


@dataclass(frozen=True)
class SessionMeta:
    sid: int
    mode: str = "ptt"  # "ptt" (hold) or "handsfree"
    target_hwnd: int = 0
    insert: bool = True  # False: dry run (tests, `keynone transcribe`)


@dataclass
class SessionOutcome:
    sid: int
    status: str  # inserted | copied | empty | cancelled | error | dry_run | not_inserted
    text: str = ""
    raw_text: str = ""
    audio_seconds: float = 0.0
    latency_ms: int = 0  # stop -> text delivered
    first_text_ms: int = 0  # first audio -> first live text
    live_lag_p50_ms: int = 0  # word spoken -> word shown in the overlay
    live_lag_p95_ms: int = 0
    live_lag_max_ms: int = 0
    llm_calls: int = 0
    message: str = ""


def _rms(pcm: bytes) -> tuple[float, int]:
    samples = np.frombuffer(pcm, dtype=np.int16)
    if not samples.size:
        return 0.0, 0
    as_float = samples.astype(np.float32)
    return float(np.sqrt(np.mean(as_float * as_float)) / 32768.0), int(np.abs(samples).max())


class Session:
    def __init__(
        self,
        meta: SessionMeta,
        *,
        config: Config,
        models: EngineModels,
        overlay: Overlay,
        inserter: Inserter | None,
        history: History | None,
        on_finished: Callable[[SessionOutcome], None],
        on_event: Callable[..., None] | None = None,
    ) -> None:
        self.meta = meta
        self.config = config
        self.models = models
        self.overlay = overlay
        self.inserter = inserter
        self.history = history
        self._on_finished = on_finished
        self._on_event = on_event or (lambda *a, **k: None)
        self._queue: queue.Queue[bytes | str] = queue.Queue()
        self._mode = meta.mode
        self._stopped_at: float | None = None
        self._first_audio: float | None = None
        self._first_text_ms = 0
        self._lags: list[float] = []
        self._peak = 0
        self._audio_bytes = 0
        self._last_voice = time.monotonic()
        self._pipeline: CleanupPipeline | None = None
        self._segmenter: SentenceSegmenter | None = None
        self.done = threading.Event()
        start_target = window_info(meta.target_hwnd or None)
        self.profile = config.profile_for(start_target.exe, start_target.title)
        self._thread = threading.Thread(target=self._run, name=f"session-{meta.sid}", daemon=True)

    # ----------------------------------------------------------------- control (IPC thread)

    def start(self) -> None:
        self.overlay.show(self.meta.target_hwnd)
        ready = self.models.asr_ready.is_set()
        self.overlay.update(status=self._listening_label() if ready else
                            "Starting up - already recording, keep talking",
                            text="", tone="listening", level=0.0)
        self._thread.start()

    def _listening_label(self) -> str:
        return "Hands-free - tap the hotkey to finish" if self._mode == "handsfree" else "Listening"

    def feed(self, pcm: bytes) -> None:
        now = time.monotonic()
        if self._first_audio is None:
            self._first_audio = now
        level, peak = _rms(pcm)
        self._peak = max(self._peak, peak)
        self._audio_bytes += len(pcm)
        if level > 0.012:
            self._last_voice = now
        self.overlay.update(level=level)
        self._queue.put(pcm)
        limit = self.config.audio.handsfree_silence_stop_seconds
        if self._mode == "handsfree" and limit > 0 and now - self._last_voice > limit:
            self._on_event("auto_stop", sid=self.meta.sid)
            self._last_voice = now  # ask once per silence stretch
        if self._audio_bytes == 32000 * 3 and self._peak == 0:
            self.overlay.update(status="Microphone is silent - check Windows microphone privacy "
                                "settings", tone="error")

    def set_mode(self, mode: str) -> None:
        self._mode = mode
        self.overlay.update(status=self._listening_label())

    def stop(self) -> None:
        self._stopped_at = time.monotonic()
        self._queue.put(_STOP)

    def cancel(self) -> None:
        self._queue.put(_CANCEL)

    # ----------------------------------------------------------------- worker

    def _run(self) -> None:
        try:
            outcome = self._process()
        except Exception as exc:
            log.exception("session %s failed", self.meta.sid)
            self.overlay.update(status=f"Error: {exc}", tone="error")
            self.overlay.hide(3000)
            outcome = SessionOutcome(self.meta.sid, "error", message=str(exc))
        finally:
            self.done.set()
        self._on_finished(outcome)

    def _refresh(self) -> None:
        if self._pipeline is None or self._segmenter is None:
            return
        text = f"{self._pipeline.preview()} {self._segmenter.pending}".strip()
        if text and not self._first_text_ms and self._first_audio is not None:
            self._first_text_ms = int((time.monotonic() - self._first_audio) * 1000)
        self.overlay.update(text=text)

    def _cancelled(self) -> SessionOutcome:
        if self._pipeline:
            self._pipeline.cancel()
        self.overlay.update(status="Cancelled", tone="idle")
        self.overlay.hide(600)
        return SessionOutcome(self.meta.sid, "cancelled")

    def _process(self) -> SessionOutcome:
        recognizer = self.models.wait_asr()
        if recognizer is None:
            message = self.models.errors[0] if self.models.errors else "Speech model unavailable"
            self.overlay.update(status=message, tone="error")
            self.overlay.hide(4000)
            return SessionOutcome(self.meta.sid, "error", message=message)
        self.overlay.update(status=self._listening_label())
        cfg = self.config
        settings = CleanupSettings(
            llm_mode=self.profile.llm_mode or cfg.cleanup.llm_mode,
            rules=RuleOptions(
                remove_fillers=cfg.cleanup.remove_fillers,
                voice_commands=cfg.cleanup.voice_commands,
                words=tuple(cfg.dictionary.words),
                replacements=tuple(cfg.dictionary.replacements.items()),
            ),
            llm_timeout=cfg.cleanup.llm_timeout_seconds,
        )
        polisher = self.models.lazy_polisher if self.models.llm_enabled else None
        stream = recognizer.stream()
        self._segmenter = SentenceSegmenter()
        self._pipeline = CleanupPipeline(settings, polisher, on_update=self._refresh)

        stopped = False
        while not stopped:
            item = self._queue.get()
            if item == _CANCEL:
                return self._cancelled()
            if item == _STOP:
                break
            chunks = [item]
            while True:  # coalesce a backlog (cold start) into one decode call
                try:
                    nxt = self._queue.get_nowait()
                except queue.Empty:
                    break
                if isinstance(nxt, bytes):
                    chunks.append(nxt)
                elif nxt == _CANCEL:
                    return self._cancelled()
                else:
                    stopped = True
                    break
            assert isinstance(item, bytes)
            before = stream.text
            update = stream.accept_pcm16(b"".join(c for c in chunks if isinstance(c, bytes)))
            if update:
                text, endpoint = update
                if text != before and self._first_audio is not None:
                    spoken_at = self._first_audio + stream.last_token_time
                    self._lags.append(time.monotonic() - spoken_at)
                for sentence in self._segmenter.update(text, flush=endpoint):
                    self._pipeline.add_sentence(sentence)
                self._refresh()

        self.overlay.update(status="Polishing", tone="working")
        raw = stream.finish()
        for sentence in self._segmenter.update(raw, flush=True):
            self._pipeline.add_sentence(sentence)
        result = self._pipeline.finish(timeout=cfg.cleanup.llm_timeout_seconds + 0.5)
        return self._deliver(raw, result.text, result.llm_calls, stream.seconds)

    def _deliver(self, raw: str, cleaned: str, llm_calls: int, seconds: float) -> SessionOutcome:
        target: WindowInfo = window_info()  # wherever the user is now
        profile = self.config.profile_for(target.exe, target.title)
        trailing = (profile.trailing_space if profile.trailing_space is not None
                    else self.config.insert.trailing_space)
        text = formatting.finalize(
            cleaned,
            style=profile.style,
            drop_final_period=bool(profile.drop_final_period),
            trailing_space=trailing,
        )
        lags = sorted(self._lags)
        base = dict(sid=self.meta.sid, raw_text=raw, audio_seconds=seconds, llm_calls=llm_calls,
                    first_text_ms=self._first_text_ms,
                    live_lag_p50_ms=int(1000 * lags[len(lags) // 2]) if lags else 0,
                    live_lag_p95_ms=int(1000 * lags[int(0.95 * (len(lags) - 1))]) if lags else 0,
                    live_lag_max_ms=int(1000 * lags[-1]) if lags else 0)
        if not text.strip():
            self.overlay.update(status="No speech detected", tone="idle", text="")
            self.overlay.hide(1200)
            return SessionOutcome(status="empty", **base)  # type: ignore[arg-type]
        self.overlay.update(text=text.strip())
        if not self.meta.insert or self.inserter is None:
            self.overlay.update(status="Done (dry run)", tone="done")
            self.overlay.hide(800)
            return SessionOutcome(status="dry_run", text=text, latency_ms=self._latency(),
                                  **base)  # type: ignore[arg-type]
        inserted = self.inserter.insert(text, profile, target)
        latency = self._latency()
        if inserted.inserted:
            self.overlay.update(status="Inserted", tone="done")
            self.overlay.hide(700)
            status = "inserted"
        else:
            self.overlay.update(status=inserted.message, tone="error")
            self.overlay.hide(3500)
            status = "copied" if inserted.method == "clipboard" else "not_inserted"
        if self.history is not None:
            # Window titles can contain e-mail subjects/document names: not stored.
            self.history.add(app=target.exe, title="", raw_text=raw,
                             final_text=text, audio_seconds=seconds, latency_ms=latency,
                             llm_calls=llm_calls, method=inserted.method)
        return SessionOutcome(status=status, text=text, latency_ms=latency,
                              message=inserted.message, **base)  # type: ignore[arg-type]

    def _latency(self) -> int:
        if self._stopped_at is None:
            return 0
        return int((time.monotonic() - self._stopped_at) * 1000)
