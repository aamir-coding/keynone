"""Incremental cleanup of a dictation, sentence by sentence.

While the user is still speaking, every finished sentence is cleaned with the
instant rules and - when it needs it - polished by the local LLM in a
background thread. Sentences that continue or correct the previous one
("... Actually, make that Friday.") are merged into one *unit* so the LLM sees
the correction together with what it corrects. When the user stops, usually
only the last unit is left to process, which keeps the release-to-text delay
short even for long dictations.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from keynone.cleanup import formatting, guard, rules
from keynone.config import LlmMode

log = logging.getLogger(__name__)

_TERMINAL = (".", "!", "?", "…", ":", "\n")


class PolishResult(Protocol):
    text: str
    truncated: bool


class Polisher(Protocol):
    def polish(
        self,
        transcript: str,
        *,
        deadline: float | None = None,
        on_text: Callable[[str], None] | None = None,
    ) -> PolishResult: ...


@dataclass(frozen=True)
class CleanupSettings:
    llm_mode: LlmMode = "auto"
    rules: rules.RuleOptions = field(default_factory=rules.RuleOptions)
    llm_timeout: float = 4.0
    max_unit_words: int = 60


@dataclass
class CleanupResult:
    text: str
    units: int = 0
    llm_calls: int = 0
    llm_accepted: int = 0
    rejections: list[str] = field(default_factory=list)
    timed_out_units: int = 0


@dataclass
class _Unit:
    raw: str
    version: int = 0
    done_version: int = -1
    polished: str = ""
    is_break: bool = False


def _rules_finalize(raw: str) -> str:
    return rules.format_spoken_list(raw) or raw


def _word_count(text: str) -> int:
    return len(text.split())


class CleanupPipeline:
    def __init__(
        self,
        settings: CleanupSettings,
        polisher: Polisher | None = None,
        on_update: Callable[[], None] | None = None,
    ) -> None:
        self.settings = settings
        self.polisher = polisher
        self._on_update = on_update
        self._units: list[_Unit] = []
        self._cond = threading.Condition()
        self._closing = False
        self._cancelled = False
        self._deadline: float | None = None
        self._result = CleanupResult(text="")
        self._worker = threading.Thread(target=self._run, name="cleanup", daemon=True)
        self._worker.start()

    # ----------------------------------------------------------------- input

    def _needs_llm(self, text: str) -> bool:
        if self.polisher is None or self.settings.llm_mode == "off":
            return False
        correction = rules.has_correction_cue(text)
        if self.settings.llm_mode == "always":
            # Deterministic list formatting beats letting the LLM restyle a list.
            return correction or rules.format_spoken_list(text) is None
        return correction or rules.has_soft_fillers(text)

    def add_sentence(self, sentence: str) -> None:
        """Feed one finished ASR sentence (raw, punctuated model output)."""
        cleaned = rules.clean_sentence(sentence, self.settings.rules)
        body, dangling = rules.resolve_scratches(cleaned)
        with self._cond:
            for _ in range(dangling):
                self._scratch_previous_locked()
            body = body.strip(" ")
            if body.strip("\n"):
                self._append_locked(body)
            elif body:  # only line breaks
                self._units.append(_Unit(raw=body, is_break=True))
            self._cond.notify_all()
        self._notify()

    def _append_locked(self, body: str) -> None:
        prev = self._units[-1] if self._units and not self._units[-1].is_break else None
        if prev is not None:
            joined = f"{prev.raw} {body}"
            continues = not prev.raw.rstrip().endswith(_TERMINAL) and body[:1].islower()
            corrects = rules.starts_with_correction_cue(body)
            lists = rules.starts_list_item(body) and rules.has_list_cue(joined)
            # "We need three things. First, ..." -> the short intro heads the list.
            opens_list = rules.starts_first_list_item(body) and _word_count(prev.raw) <= 25
            if (continues or corrects or lists or opens_list) and _word_count(joined) <= (
                self.settings.max_unit_words
            ):
                prev.raw = joined
                prev.version += 1
                return
        self._units.append(_Unit(raw=body))

    def _scratch_previous_locked(self) -> None:
        while self._units and self._units[-1].is_break:
            self._units.pop()
        if not self._units:
            return
        unit = self._units[-1]
        unit.raw = rules.drop_last_sentence(unit.raw)
        unit.version += 1
        if not unit.raw.strip():
            self._units.pop()

    # ----------------------------------------------------------------- worker

    def _pending_locked(self) -> _Unit | None:
        for unit in self._units:
            if unit.done_version != unit.version:
                return unit
        return None

    def _run(self) -> None:
        while True:
            with self._cond:
                unit = self._pending_locked()
                while unit is None and not (self._closing or self._cancelled):
                    self._cond.wait()
                    unit = self._pending_locked()
                if self._cancelled or unit is None:
                    return
                raw, version = unit.raw, unit.version
                deadline = self._deadline
            text = self._process(raw, deadline)
            with self._cond:
                if unit.version == version:
                    unit.polished = text
                    unit.done_version = version
                self._cond.notify_all()
            self._notify()

    def _process(self, raw: str, finish_deadline: float | None) -> str:
        if unit_is_break(raw):
            return raw
        raw = rules.resolve_corrections(raw)
        fallback = _rules_finalize(raw)
        if self.polisher is None or not self._needs_llm(raw):
            return fallback
        deadline = time.monotonic() + self.settings.llm_timeout
        if finish_deadline is not None:
            deadline = min(deadline, finish_deadline)
        self._result.llm_calls += 1
        try:
            result = self.polisher.polish(raw, deadline=deadline)
        except Exception as exc:  # LLM problems must never lose the dictation
            log.warning("LLM polish failed: %s", exc)
            self._result.rejections.append(f"error: {exc}")
            return fallback
        if result.truncated:
            self._result.rejections.append("timeout")
            return fallback
        verdict = guard.validate(raw, result.text, has_correction=rules.has_correction_cue(raw))
        if not verdict.ok:
            log.info("LLM output rejected: %s", verdict.reason)
            self._result.rejections.append(verdict.reason)
            return fallback
        self._result.llm_accepted += 1
        polished = rules.tidy(verdict.text)
        polished = rules.apply_dictionary(
            polished, self.settings.rules.words, dict(self.settings.rules.replacements)
        )
        return _rules_finalize(polished)

    def _notify(self) -> None:
        if self._on_update:
            try:
                self._on_update()
            except Exception:
                log.exception("cleanup update callback failed")

    # ----------------------------------------------------------------- output

    def preview(self) -> str:
        """Best text so far, for the live overlay."""
        with self._cond:
            parts = [u.polished if u.done_version == u.version else u.raw for u in self._units]
        return formatting.join_units(parts)

    def finish(self, timeout: float) -> CleanupResult:
        """Wait (at most ``timeout`` s) for pending units and return the text."""
        deadline = time.monotonic() + timeout
        with self._cond:
            self._closing = True
            self._deadline = deadline
            self._cond.notify_all()
            while self._pending_locked() is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._cond.wait(remaining)
            parts = []
            for unit in self._units:
                if unit.done_version == unit.version:
                    parts.append(unit.polished)
                else:
                    self._result.timed_out_units += 1
                    parts.append(_rules_finalize(unit.raw))
            self._result.units = sum(1 for u in self._units if not u.is_break)
        self._result.text = formatting.join_units(parts)
        return self._result

    def cancel(self) -> None:
        with self._cond:
            self._cancelled = True
            self._cond.notify_all()


def unit_is_break(text: str) -> bool:
    return bool(text) and not text.strip("\n")
