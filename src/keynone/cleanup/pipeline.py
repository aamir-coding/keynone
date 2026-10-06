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
_LEADING_PUNCT_RE = re.compile(r"^([.!?…,;:]+)\s*")
# A command cut in two by a pause: "... Scratch" | "that I'll call you tonight".
_COMMAND_HEAD_RE = re.compile(
    r"(?i)(?:^|\s)(?P<head>(?:(?:no|oh)[,.]?\s+)?(?:scratch|strike|delete|erase)|new|next)[.,]?$"
)
_COMMAND_TAIL_RE = {
    "scratch": re.compile(r"(?i)^(?:that|this)\b"),
    "break": re.compile(r"(?i)^(?:paragraph|para|line)s?\b"),
}


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
    # Where each pause-separated fragment starts in ``raw``: "scratch that"
    # removes what was said since the last pause, not a whole run-on sentence.
    fragments: list[int] = field(default_factory=lambda: [0])


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
        if language != "en":
            if self.settings.llm_mode == "always":
                return True
            # Only sentences with a correction/filler cue: small models are less
            # reliable outside English, so plain sentences keep the ASR text.
            return (rules.has_correction_cue(text, language)
                    or rules.has_soft_fillers(text, language))
        # A restarted phrase ("I think we should, I think we must"), unless it is
        # just the markers of a list the rules already formatted.
        restart = rules.has_restart(text) and rules.format_spoken_list(text) is None
        correction = rules.has_correction_cue(text) or restart
        if self.settings.llm_mode == "always":
            # Deterministic list formatting beats letting the LLM restyle a list.
            return correction or rules.format_spoken_list(text) is None
        return (correction or rules.has_soft_fillers(text)
                or (rules.is_run_on(text) and rules.format_spoken_list(text) is None))

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
                prev = self._last_text_unit_locked()
                if prev is not None and not prev.raw.rstrip().endswith((*_TERMINAL, ",")):
                    prev.raw = prev.raw.rstrip() + leading.group(1)
                    prev.version += 1
                raw = raw[leading.end() :]
            if raw:
                if self.settings.rules.voice_commands:
                    self._add_with_commands_locked(self._rejoin_command_locked(raw))
                else:
                    self._append_locked(raw)
            self._cond.notify_all()
        self._notify()

    def _last_text_unit_locked(self) -> _Unit | None:
        return self._units[-1] if self._units and not self._units[-1].is_break else None

    def _set_raw_locked(self, unit: _Unit, raw: str) -> None:
        unit.raw = raw
        unit.fragments = [f for f in unit.fragments if f < len(raw)] or [0]
        unit.version += 1

    def _rejoin_command_locked(self, raw: str) -> str:
        """Take back a command's first word that the pause left in the previous unit."""
        prev = self._last_text_unit_locked()
        if prev is None:
            return raw
        head = _COMMAND_HEAD_RE.search(prev.raw)
        if head is None:
            return raw
        word = head.group("head").split()[-1].lower()
        kind = "break" if word in ("new", "next") else "scratch"
        if not _COMMAND_TAIL_RE[kind].match(raw):
            return raw
        self._set_raw_locked(prev, prev.raw[: head.start("head")].rstrip(" ,"))
        if not prev.raw.strip():
            self._units.remove(prev)
        return f"{head.group('head')} {raw}"

    def _add_with_commands_locked(self, raw: str) -> None:
        command = rules.command_for(raw)
        if command == rules.SCRATCH:
            self._scratch_locked(None)
            return
        if command is not None:
            self._close_open_sentence_locked()
            self._units.append(_Unit(raw=command, is_break=True))
            return
        for _ in range(4):  # "... scratch that, scratch that, ..." one cue at a time
            if self._unit_language(raw) != "en":
                break
            cue = next((c for c in rules.find_cues(raw) if c.scratch), None)
            if cue is None:
                break
            before = raw[: cue.start].rstrip(" ,;:")
            repair = raw[cue.end :].lstrip(" ,.;:!")
            if not rules.has_content(before):  # "Scratch that, I'll call you tonight."
                again = next((c for c in rules.find_cues(repair) if c.scratch), None)
                if again is not None and not rules.has_content(repair[: again.start]):
                    self._scratch_locked(None)  # "scratch that, scratch that, ..."
                    raw = repair
                    continue
                self._scratch_locked(repair or None)
                return
            raw = self._scratch_within_locked(before, repair)  # "... scratch that ..."
            if not raw:
                return
        self._append_locked(raw)

    def _scratch_within_locked(self, before: str, repair: str) -> str:
        """"<before> scratch that <repair>" in one fragment: correct, or drop the
        last sentence of ``before`` (what was said since the last sentence end)."""
        if not repair:  # "... new paragraph scratch that": only the break goes
            kept = rules.strip_trailing_break(before)
            if kept is not None:
                return kept
        start = rules.last_sentence_start(before)
        head, before = before[:start], before[start:]
        prev = self._last_text_unit_locked()
        if not head and prev is not None and self._continues_locked(prev, before):
            begin = rules.last_sentence_start(prev.raw)
            if repair:
                replaced = rules.correct(f"{prev.raw[begin:]} {before}", repair, rules.STRONG)
                if replaced is not None:
                    self._set_raw_locked(prev, (prev.raw[:begin] + replaced).strip())
                    return ""
            elif rules.is_unfinished(prev.raw[begin:]):
                # "The budget is [pause] too high scratch that": all of it goes
                self._set_raw_locked(prev, prev.raw[:begin].rstrip())
                if not prev.raw:
                    self._units.remove(prev)
                return ""
        replaced = rules.correct(before, repair, rules.STRONG) if repair else None
        return f"{head}{replaced or repair}".strip()

    def _scratch_locked(self, repair: str | None) -> None:
        """"Scratch that": remove what was said last (since the previous pause or the
        end of the previous sentence) - or correct it, when ``repair`` fits."""
        # A lead-in without content ("Uh, you know what" [pause] "scratch that") is
        # not what the speaker wants gone: it goes, and the scratch reaches past it.
        while self._units and not self._units[-1].is_break:
            unit = self._units[-1]
            cut = max(rules.last_sentence_start(unit.raw), unit.fragments[-1])
            if rules.has_content(unit.raw[cut:]):
                break
            self._set_raw_locked(unit, unit.raw[:cut].rstrip(" ,;:"))
            if not unit.raw.strip():
                self._units.remove(unit)
        if self._units and self._units[-1].is_break:
            self._units.pop()  # the last thing said was "new paragraph"
        elif self._units:
            unit = self._units[-1]
            start = rules.last_sentence_start(unit.raw)
            if repair:
                replaced = rules.correct(unit.raw[start:], repair, rules.STRONG)
                if replaced is not None:
                    self._set_raw_locked(unit, (unit.raw[:start] + replaced).strip())
                    return
            cut = max(start, unit.fragments[-1])
            if rules.is_unfinished(unit.raw[start:cut]):
                cut = start  # "First, [pause] milk. Scratch that." - "First," goes too
            self._set_raw_locked(unit, unit.raw[:cut].rstrip(" ,;:"))
            if not unit.raw.strip():
                self._units.remove(unit)
        if repair:  # "scratch that, I'll call you tonight" vs "... scratch that, tonight"
            self._append_locked(repair, new_sentence=rules.is_clause(repair))

    def _close_open_sentence_locked(self) -> None:
        """End the last unit's sentence before a spoken line/paragraph break."""
        prev = self._last_text_unit_locked()
        if prev is None or prev.raw.rstrip().endswith((*_TERMINAL, ",")):
            return
        closed = rules.close_before_break(prev.raw)
        if closed != prev.raw:
            prev.raw = closed
            prev.version += 1

    def _continues_locked(self, prev: _Unit, raw: str) -> bool:
        """``raw`` (after a pause) continues the open sentence at the end of ``prev``."""
        tail = prev.raw.rstrip()
        if tail.endswith(_TERMINAL):
            return False
        if rules.starts_like_sentence(raw):
            return False  # "... is ready [pause] did you see it", "[pause] let's go"
        if rules.ends_list_item(prev.raw) and rules.starts_clause(raw):
            return False  # "third, bread [pause] that's all for now"
        return (raw[:1].islower() or tail.endswith(",")
                or rules.ends_with_non_final_word(tail)
                or rules.ends_with_correction_cue(tail)
                # A name after a pause, which the recognizer did not end with a
                # period: "I invited Claude [pause] Gemini and Llama".
                or rules.starts_with_name(raw))

    def _append_locked(self, raw: str, *, new_sentence: bool = False) -> None:
        prev = self._last_text_unit_locked()
        if prev is not None:
            in_list = rules.continues_list(prev.raw, raw)
            limit = self.settings.max_unit_words * (3 if in_list else 1)
            fits = _word_count(prev.raw) + _word_count(raw) <= limit
            tail = prev.raw.rstrip()
            closed = tail if tail.endswith(_TERMINAL) else rules.close_sentence(tail)
            if fits and not new_sentence:
                # The pause before "no, ..." / "sorry, ..." works like a comma, and a
                # sentence-ending correction ("... at three. No, four.") joins too -
                # also a cue said on its own ("Send it to John." [pause] "I mean").
                if rules.starts_with_correction_cue(raw) or rules.is_bare_cue(raw):
                    head = tail if tail.endswith((*_TERMINAL, ",")) else tail.rstrip(";:") + ","
                    self._merge_locked(prev, head, raw)
                    return
                # The pause before a list marker ends the intro or the previous item:
                # "We need three things [pause] first eggs [pause] second milk".
                opens_list = rules.starts_first_list_item(raw) and _word_count(prev.raw) <= 25
                if rules.starts_list_item(raw) and (in_list or opens_list):
                    self._merge_locked(prev, closed, raw)
                    return
                if self._continues_locked(prev, raw):
                    self._merge_locked(prev, tail, raw)  # a pause mid-sentence
                    return
                if in_list:  # the item after "Number two." [pause] "A car."
                    self._merge_locked(prev, closed, raw)
                    return
            # Otherwise the pause ended a sentence (maybe left unpunctuated by the
            # recognizer: the period is added when units are joined).
        self._units.append(_Unit(raw=raw))

    @staticmethod
    def _merge_locked(unit: _Unit, head: str, fragment: str) -> None:
        unit.raw = f"{head} {fragment}"
        unit.fragments.append(len(head) + 1)
        unit.version += 1

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
                                     has_correction=rules.has_correction_cue(cleaned, language),
                                     strict=language != "en")
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
