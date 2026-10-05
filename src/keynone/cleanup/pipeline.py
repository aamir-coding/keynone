"""Incremental cleanup of a dictation, sentence by sentence.

While the user is still speaking, every finished ASR sentence is grouped into
*units* and cleaned in a background thread: fragments split by a pause are
re-joined, a sentence that corrects the previous one ("... Actually, make
that Friday.") joins it, and a spoken list stays together. Each unit gets the
instant rules and - only when it needs it - the local LLM. When the user
stops, usually only the last unit is left to process, which keeps the
release-to-text delay short even for long dictations.
"""

from __future__ import annotations

import dataclasses
import logging
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from keynone.cleanup import formatting, guard, rules
from keynone.cleanup.language import base_language, detect_language, language_code, language_name
from keynone.cleanup.prompts import PromptVariant
from keynone.config import LlmMode

log = logging.getLogger(__name__)

_TERMINAL = (".", "!", "?", "…", ":", "\n")
_LEADING_SCRATCH_RE = re.compile(
    r"(?i)^(?:scratch|delete|strike|erase)\s+that[,.!:;]?\s+(?P<rest>\S.*)$"
)
_GREETING_RE = re.compile(r"(?i)^(?:hi|hello|hey|dear|good (?:morning|afternoon|evening))\b")
_LEADING_PUNCT_RE = re.compile(r"^([.!?…,;:]+)\s*")


class PolishResult(Protocol):
    text: str
    truncated: bool


class Polisher(Protocol):
    def polish(
        self,
        transcript: str,
        *,
        variant: PromptVariant = "en",
        target: str = "English",
        deadline: float | None = None,
        on_text: Callable[[str], None] | None = None,
    ) -> PolishResult: ...


@dataclass(frozen=True)
class CleanupSettings:
    llm_mode: LlmMode = "auto"
    rules: rules.RuleOptions = field(default_factory=rules.RuleOptions)
    llm_timeout: float = 4.0
    max_unit_words: int = 60
    # "en" (English only), "auto" (detect per sentence) or a language code.
    language: str = "en"
    # Target language name or code ("English", "es" ...); "" = no translation.
    translate_to: str = ""


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
    raw: str  # recognizer text; cleaned as a whole when processed
    version: int = 0
    done_version: int = -1
    polished: str = ""
    is_break: bool = False


def _word_count(text: str) -> int:
    return len(text.split())


def unit_is_break(text: str) -> bool:
    return bool(text) and not text.strip("\n")


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

    # ----------------------------------------------------------------- language

    def _unit_language(self, text: str) -> str:
        """Language of one unit: fixed for English setups, detected otherwise."""
        configured = base_language(self.settings.language)
        if configured == "en":
            return "en"
        return detect_language(text, default="en" if configured in ("auto", "") else configured)

    def _translation_target(self) -> tuple[str, str] | None:
        """(code, display name) of ``translate_to``, or None when not translating."""
        target = self.settings.translate_to.strip()
        if not target:
            return None
        code = language_code(target)
        name = language_name(code)
        return code, target if name == code else name  # keep unknown names as typed

    # ----------------------------------------------------------------- rules

    def _rules_clean(self, raw: str, language: str) -> str:
        """Deterministic cleanup of one unit (instant)."""
        options = dataclasses.replace(self.settings.rules, language=language)
        text = rules.clean_sentence(raw, options)
        text, _ = rules.resolve_scratches(text)
        return rules.resolve_corrections(text) if language == "en" else text

    @staticmethod
    def _finalize(text: str, language: str) -> str:
        if language != "en":  # spoken-list detection understands English ordinals only
            return text
        return rules.format_spoken_list(text) or text

    def _needs_llm(self, text: str, language: str) -> bool:
        if self.polisher is None or self.settings.llm_mode == "off":
            return False
        target = self._translation_target()
        if target is not None and target[0] != language:
            return True
        if language != "en":  # rules are English-only: the LLM does the rest
            return True
        correction = rules.has_correction_cue(text)
        if self.settings.llm_mode == "always":
            # Deterministic list formatting beats letting the LLM restyle a list.
            return correction or rules.format_spoken_list(text) is None
        return correction or rules.has_soft_fillers(text)

    # ----------------------------------------------------------------- input

    def add_sentence(self, sentence: str) -> None:
        """Feed one finished ASR sentence (or a fragment ended by a pause)."""
        raw = " ".join(sentence.split())
        if not raw:
            return
        with self._cond:
            # The recognizer often emits a sentence's final punctuation only after
            # the pause that flushed the sentence: give it back to that sentence.
            leading = _LEADING_PUNCT_RE.match(raw)
            if leading:
                prev = self._units[-1] if self._units and not self._units[-1].is_break else None
                if prev is not None and not prev.raw.rstrip().endswith((*_TERMINAL, ",")):
                    prev.raw = prev.raw.rstrip() + leading.group(1)
                    prev.version += 1
                raw = raw[leading.end() :]
                if not raw:
                    self._cond.notify_all()
                    return
            commands_on = self.settings.rules.voice_commands
            command = rules.command_for(raw) if commands_on else None
            scratch_then = _LEADING_SCRATCH_RE.match(raw) if commands_on else None
            if command == rules.SCRATCH:
                self._scratch_previous_locked()
            elif command is not None:
                self._close_open_sentence_locked(before_break=True)
                self._units.append(_Unit(raw=command, is_break=True))
            elif scratch_then:
                self._scratch_previous_locked()
                self._append_locked(scratch_then.group("rest"))
            else:
                self._append_locked(raw)
            self._cond.notify_all()
        self._notify()

    def _close_open_sentence_locked(self, *, before_break: bool = False) -> _Unit | None:
        """End the last unit's sentence if the recognizer left it unpunctuated."""
        prev = self._units[-1] if self._units and not self._units[-1].is_break else None
        if prev is None or prev.raw.rstrip().endswith((*_TERMINAL, ",")):
            return prev
        body = prev.raw.rstrip(" ,;:")
        greeting = before_break and _word_count(body) <= 4 and _GREETING_RE.match(body)
        prev.raw = body + ("," if greeting else ".")
        prev.version += 1
        return prev

    def _append_locked(self, raw: str) -> None:
        prev = self._units[-1] if self._units and not self._units[-1].is_break else None
        if prev is not None:
            limit = self.settings.max_unit_words
            tail = prev.raw.rstrip()
            open_sentence = not tail.endswith(_TERMINAL)
            mid_sentence = (raw[:1].islower() or tail.endswith(",")
                            or rules.ends_with_non_final_word(tail))
            if open_sentence and mid_sentence and _word_count(prev.raw) + _word_count(raw) <= limit:
                prev.raw = f"{tail} {raw}"  # a pause in the middle of a sentence
                prev.version += 1
                return
            # Otherwise the pause ended a sentence (maybe left unpunctuated by the
            # recognizer: the period is added when units are joined).
            joined = f"{tail.rstrip(',;:')}. {raw}" if open_sentence else f"{tail} {raw}"
            corrects = rules.starts_with_correction_cue(raw)
            lists = rules.starts_list_item(raw) and rules.has_list_cue(joined)
            # "We need three things. First, ..." -> the short intro heads the list.
            opens_list = rules.starts_first_list_item(raw) and _word_count(prev.raw) <= 25
            if (corrects or lists or opens_list) and _word_count(joined) <= limit:
                prev.raw = joined
                prev.version += 1
                return
        self._units.append(_Unit(raw=raw))

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
        language = self._unit_language(raw)
        cleaned = self._rules_clean(raw, language)
        fallback = self._finalize(cleaned, language)
        if self.polisher is None or not self._needs_llm(cleaned, language):
            return fallback
        target = self._translation_target()
        translating = target is not None and target[0] != language
        variant: PromptVariant = "translate" if translating else (
            "en" if language == "en" else "multi")
        deadline = time.monotonic() + self.settings.llm_timeout
        if finish_deadline is not None:
            deadline = min(deadline, finish_deadline)
        self._result.llm_calls += 1
        try:
            result = self.polisher.polish(cleaned, variant=variant,
                                          target=target[1] if target else "English",
                                          deadline=deadline)
        except Exception as exc:  # LLM problems must never lose the dictation
            log.warning("LLM polish failed: %s", exc)
            self._result.rejections.append(f"error: {exc}")
            return fallback
        if result.truncated:
            self._result.rejections.append("timeout")
            return fallback
        if translating:
            verdict = guard.validate_translation(cleaned, result.text)
        else:
            verdict = guard.validate(cleaned, result.text,
                                     has_correction=rules.has_correction_cue(cleaned))
        if not verdict.ok:
            log.info("LLM output rejected: %s", verdict.reason)
            self._result.rejections.append(verdict.reason)
            return fallback
        self._result.llm_accepted += 1
        output_language = target[0] if translating and target else language
        polished = rules.tidy(verdict.text, output_language)
        polished = rules.apply_dictionary(
            polished, self.settings.rules.words, dict(self.settings.rules.replacements)
        )
        return self._finalize(polished, output_language)

    def _notify(self) -> None:
        if self._on_update:
            try:
                self._on_update()
            except Exception:
                log.exception("cleanup update callback failed")

    # ----------------------------------------------------------------- output

    def preview(self) -> str:
        """Best text so far, for the live overlay (instant rules for pending units)."""
        with self._cond:
            snapshot = [(u.raw, u.polished, u.done_version == u.version, u.is_break)
                        for u in self._units]
        parts = [polished if done else
                 (raw if is_break else self._rules_clean(raw, self._unit_language(raw)))
                 for raw, polished, done, is_break in snapshot]
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
            snapshot = [(u.raw, u.polished, u.done_version == u.version, u.is_break)
                        for u in self._units]
            self._result.units = sum(1 for u in self._units if not u.is_break)
        parts = []
        for raw, polished, done, is_break in snapshot:
            if done or is_break:
                parts.append(polished if done else raw)
            else:
                self._result.timed_out_units += 1
                language = self._unit_language(raw)
                parts.append(self._finalize(self._rules_clean(raw, language), language))
        self._result.text = formatting.join_units(parts)
        return self._result

    def cancel(self) -> None:
        with self._cond:
            self._cancelled = True
            self._cond.notify_all()
