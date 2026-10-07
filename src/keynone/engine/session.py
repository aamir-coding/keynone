"""One dictation (or speak-to-edit), from the first audio frame to inserted text."""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from keynone.audio_level import LevelNormalizer
from keynone.cleanup import formatting, guard, rules, snippets
from keynone.cleanup.language import detect_language
from keynone.cleanup.pipeline import CleanupPipeline, CleanupSettings
from keynone.cleanup.rules import RuleOptions
from keynone.cleanup.segmenter import SentenceSegmenter
from keynone.config import AppProfile, Config, Style
from keynone.engine.history import History
from keynone.engine.inject import Inserter
from keynone.engine.models import EngineModels
from keynone.engine.overlay import Overlay
from keynone.learning import Learned
from keynone.win32.keys import Chord
from keynone.win32.uia import UiaClient
from keynone.win32.windows import WindowInfo, window_info

log = logging.getLogger(__name__)

_STOP = "stop"
_CANCEL = "cancel"


@dataclass(frozen=True)
class SessionMeta:
    sid: int
    mode: str = "ptt"  # "ptt" (hold), "handsfree" or "edit" (speak-to-edit)
    target_hwnd: int = 0
    insert: bool = True  # False: dry run (tests, `keynone transcribe`)


@dataclass
class SessionOutcome:
    sid: int
    # inserted | copied | empty | cancelled | error | dry_run | not_inserted | no_selection
    status: str
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


def compose(cleaned: str, profile: AppProfile, config: Config, *,
            spoken_end: bool = False) -> tuple[str, Style]:
    """The text to insert for a cleaned dictation, before smart spacing, and the style
    it is written in. Snippet placeholders in it are filled in after smart spacing."""
    saved = snippets.whole(cleaned, config.snippets.items())
    if saved is not None:  # "my address": the saved text, exactly as written
        return saved, "code"
    if any(snippets.is_placeholder(c) for c in cleaned) and not any(c.isalnum() for c in cleaned):
        # "insert my address" on its own: only the saved text, without the period after it
        return "".join(c for c in cleaned if snippets.is_placeholder(c)), "code"
    style = formatting.effective_style(profile.style, cleaned)
    if style == "code" and formatting.looks_like_command(cleaned):
        cleaned = formatting.as_command(cleaned)  # "Git status." -> "git status"
    text = formatting.finalize(
        cleaned,
        style=style,
        # A period the user said ("... see you soon period") stays, even in chats.
        drop_final_period=bool(profile.drop_final_period) and not spoken_end,
        trailing_space=False,
    )
    return text, style


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
        uia: UiaClient | None = None,
        learned: Sequence[Learned] = (),
        on_inserted: Callable[[str, AppProfile], None] | None = None,
    ) -> None:
        self.meta = meta
        self.config = config
        self.models = models
        self.overlay = overlay
        self.inserter = inserter
        self.history = history
        self.uia = uia
        self.learned = tuple(learned)
        self._on_finished = on_finished
        self._on_event = on_event or (lambda *a, **k: None)
        self._on_inserted = on_inserted  # e.g. watch the text box for corrections
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
        if self._mode == "edit":
            return "Edit: say how to change the selected text"
        if self._mode == "handsfree":
            return "Hands-free - tap the hotkey to finish"
        return "Listening"

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
        if mode == "edit" and self._pipeline is not None:
            self._pipeline.cancel()  # an instruction is not polished, only cleaned at the end
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

    def _settings(self) -> CleanupSettings:
        cfg = self.config
        return CleanupSettings(
            llm_mode="off" if self._mode == "edit" else (
                self.profile.llm_mode or cfg.cleanup.llm_mode),
            rules=RuleOptions(
                remove_fillers=cfg.cleanup.remove_fillers,
                voice_commands=cfg.cleanup.voice_commands,
                words=tuple(cfg.dictionary.words),
                replacements=tuple(cfg.dictionary.replacements.items()),
            ),
            llm_timeout=cfg.cleanup.llm_timeout_seconds,
            language=cfg.asr.language,
            translate_to=cfg.cleanup.translate_to,
            spoken_punctuation=cfg.cleanup.spoken_punctuation,
            style=self.profile.style,
            snippets=tuple(cfg.snippets.items()),
            learned=self.learned,
        )

    def _process(self) -> SessionOutcome:
        recognizer = self.models.wait_asr()
        if recognizer is None:
            message = self.models.errors[0] if self.models.errors else "Speech model unavailable"
            self.overlay.update(status=message, tone="error")
            self.overlay.hide(4000)
            return SessionOutcome(self.meta.sid, "error", message=message)
        self.overlay.update(status=self._listening_label())
        cfg = self.config
        polisher = self.models.lazy_polisher if self.models.llm_enabled else None
        stream = recognizer.stream(cfg.asr.language)
        normalizer = LevelNormalizer() if cfg.audio.normalize_level else None
        self._segmenter = SentenceSegmenter()
        self._pipeline = CleanupPipeline(self._settings(), polisher, on_update=self._refresh)

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
            pcm = b"".join(c for c in chunks if isinstance(c, bytes))
            update = stream.accept_pcm16(normalizer.process(pcm) if normalizer else pcm)
            if update:
                text, endpoint = update
                if text != before and self._first_audio is not None:
                    spoken_at = self._first_audio + stream.last_token_time
                    self._lags.append(time.monotonic() - spoken_at)
                for sentence in self._segmenter.update(text, flush=endpoint):
                    self._pipeline.add_sentence(sentence)
                self._refresh()

        raw = stream.finish()
        if self._mode == "edit":
            self._pipeline.cancel()
            return self._deliver_edit(raw, stream.seconds)
        self.overlay.update(status="Polishing", tone="working")
        for sentence in self._segmenter.update(raw, flush=True):
            self._pipeline.add_sentence(sentence)
        result = self._pipeline.finish(timeout=cfg.cleanup.llm_timeout_seconds + 0.5)
        return self._deliver(raw, result.text, result.llm_calls, stream.seconds,
                             spoken_end=result.spoken_end, placeholders=result.snippets)

    # ----------------------------------------------------------------- dictation

    def _base(self, raw: str, seconds: float, llm_calls: int) -> dict[str, Any]:
        lags = sorted(self._lags)
        return dict(sid=self.meta.sid, raw_text=raw, audio_seconds=seconds, llm_calls=llm_calls,
                    first_text_ms=self._first_text_ms,
                    live_lag_p50_ms=int(1000 * lags[len(lags) // 2]) if lags else 0,
                    live_lag_p95_ms=int(1000 * lags[int(0.95 * (len(lags) - 1))]) if lags else 0,
                    live_lag_max_ms=int(1000 * lags[-1]) if lags else 0)

    def _fit_to_cursor(self, text: str, style: Style, trailing: bool) -> tuple[str, bool]:
        """Smart spacing via UI Automation; returns (text, is_password_field)."""
        context = None
        if self.uia is not None and self.config.insert.smart_spacing and text.strip(" \t"):
            context = self.uia.caret_context(timeout=0.35)
        if context is not None:
            if context.is_password:
                return text, True
            fitted = formatting.adapt_to_context(text, context.before, context.after,
                                                 style=style, trailing_space=trailing)
            return fitted, False
        if trailing and text and not text.endswith(("\n", "(", "[", "{")):
            text += " "
        return text, False

    def _deliver(self, raw: str, cleaned: str, llm_calls: int, seconds: float, *,
                 spoken_end: bool = False,
                 placeholders: dict[str, str] | None = None) -> SessionOutcome:
        target: WindowInfo = window_info()  # wherever the user is now
        profile = self.config.profile_for(target.exe, target.title)
        trailing = (profile.trailing_space if profile.trailing_space is not None
                    else self.config.insert.trailing_space)
        text, style = compose(cleaned, profile, self.config, spoken_end=spoken_end)
        base = self._base(raw, seconds, llm_calls)
        if not text.strip(" \t"):  # just "new paragraph" is something to insert
            self.overlay.update(status="No speech detected", tone="idle", text="")
            self.overlay.hide(1200)
            return SessionOutcome(status="empty", **base)
        filled = placeholders or {}
        self.overlay.update(text=snippets.fill(text, filled).strip())
        if not self.meta.insert or self.inserter is None:
            text = snippets.fill(text, filled)
            if trailing and not text.endswith("\n"):
                text += " "
            self.overlay.update(status="Done (dry run)", tone="done")
            self.overlay.hide(800)
            return SessionOutcome(status="dry_run", text=text, latency_ms=self._latency(),
                                  **base)
        text, password = self._fit_to_cursor(text, style, trailing)
        text = snippets.fill(text, filled)  # after smart spacing: the saved text stays as is
        if password:
            self.overlay.update(status="Password field - not inserted", tone="error")
            self.overlay.hide(3500)
            return SessionOutcome(status="not_inserted", text="", latency_ms=self._latency(),
                                  message="password field", **base)
        if not text:  # "new paragraph" where the cursor already starts one
            self.overlay.update(status="Already on a new line", tone="done")
            self.overlay.hide(700)
            return SessionOutcome(status="empty", latency_ms=self._latency(), **base)
        inserted = self.inserter.insert(text, profile, target)
        outcome = self._finish_insert(inserted, text, raw, seconds, llm_calls, target, base,
                                      success="Inserted")
        if inserted.inserted and self._on_inserted is not None:
            try:
                self._on_inserted(text, profile)
            except Exception:  # e.g. watching for corrections: never worth failing for
                log.warning("after-insert hook failed", exc_info=True)
        return outcome

    def _finish_insert(self, inserted: Any, text: str, raw: str, seconds: float, llm_calls: int,
                       target: WindowInfo, base: dict[str, Any], *, success: str
                       ) -> SessionOutcome:
        latency = self._latency()
        if inserted.inserted:
            self.overlay.update(status=success, tone="done")
            self.overlay.hide(700)
            status = "inserted"
        else:
            self.overlay.update(status=inserted.message, tone="error")
            self.overlay.hide(3500)
            status = "copied" if inserted.method == "clipboard" else "not_inserted"
        if self.history is not None:
            # Window titles can contain e-mail subjects/document names: not stored.
            method = "edit" if self._mode == "edit" else inserted.method
            self.history.add(app=target.exe, title="", raw_text=raw, final_text=text,
                             audio_seconds=seconds, latency_ms=latency, llm_calls=llm_calls,
                             method=method)
        return SessionOutcome(status=status, text=text, latency_ms=latency,
                              message=inserted.message, **{**base, "llm_calls": llm_calls})

    # ----------------------------------------------------------------- speak-to-edit

    def _read_selection(self, profile: AppProfile) -> str | None:
        """Selected text of the focused app: UI Automation first, else the clipboard.

        Code editors copy the whole line when nothing is selected, so there only
        a selection confirmed by UI Automation is used.
        """
        selection = self.uia.selected_text(timeout=0.6) if self.uia is not None else None
        if selection:
            return selection
        if profile.style in ("code", "prompt") or self.inserter is None:
            return selection
        return self.inserter.copy_selection(profile)

    def _edit_message(self, status: str, message: str, base: dict[str, Any]) -> SessionOutcome:
        self.overlay.update(status=message, tone="error")
        self.overlay.hide(4000)
        return SessionOutcome(status=status, message=message, latency_ms=self._latency(), **base)

    def _deliver_edit(self, raw: str, seconds: float) -> SessionOutcome:
        language = detect_language(raw)
        if language == "en":  # "make it shorter, no, more formal" / "... scratch that, ..."
            instruction = rules.clean_instruction(raw)
        else:
            instruction = rules.clean_sentence(
                raw, RuleOptions(voice_commands=False, language=language)).strip()
        base = self._base(raw, seconds, 0)
        if not instruction:
            self.overlay.update(status="No instruction heard", tone="idle", text="")
            self.overlay.hide(1500)
            return SessionOutcome(status="empty", **base)
        self.overlay.update(text=instruction)
        if not self.meta.insert or self.inserter is None:
            self.overlay.update(status="Done (dry run)", tone="done")
            self.overlay.hide(800)
            return SessionOutcome(status="dry_run", text=instruction, **base)
        if not self.models.llm_enabled:
            return self._edit_message("error", "Speak-to-edit needs the local language model",
                                      base)
        target = window_info()
        profile = self.config.profile_for(target.exe, target.title)
        self.overlay.update(status="Reading the selection", tone="working")
        selection = self._read_selection(profile)
        if not selection or not selection.strip():
            chord = Chord.parse(self.config.hotkeys.edit or "ctrl+win+alt")
            return self._edit_message(
                "no_selection", f"Select some text first, then hold {chord} and say what to "
                "change", base)
        # The selection and its rewrite must both fit in one LLM slot (~3.5 chars/token).
        context_limit = int((self.config.llm.context - 256) / 2 * 3.5)
        limit = min(self.config.edit.max_selection_chars, context_limit)
        if len(selection) > limit:
            return self._edit_message("error", f"Selection too long ({len(selection)} > "
                                      f"{limit} characters)", base)
        self.overlay.update(status=f"Rewriting: {instruction}", tone="working")
        deadline = time.monotonic() + self.config.edit.timeout_seconds
        result = self.models.lazy_polisher.rewrite(
            instruction, selection, deadline=deadline,
            on_text=lambda partial: self.overlay.update(text=partial))
        if result.truncated:
            return self._edit_message("error", "Rewrite timed out - try a shorter selection",
                                      base)
        verdict = guard.validate_rewrite(selection, result.text)
        if not verdict.ok:
            return self._edit_message("error", f"Rewrite not applied: {verdict.reason}", base)
        self.overlay.update(text=verdict.text.strip())
        inserted = self.inserter.insert(verdict.text, profile, target)  # replaces the selection
        return self._finish_insert(inserted, verdict.text, instruction, seconds, 1, target, base,
                                   success="Replaced")

    def _latency(self) -> int:
        if self._stopped_at is None:
            return 0
        return int((time.monotonic() - self._stopped_at) * 1000)
