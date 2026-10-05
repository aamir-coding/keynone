"""Guardrails for LLM output.

Small local models occasionally answer a dictated question, follow a dictated
instruction ("translate this ..."), invent words, or drop half the text. The
guard compares the LLM output with its input and rejects anything that does
not look like a faithful cleanup; the caller then falls back to the
deterministic rule-based text, so a bad generation can never reach the user.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_WORD_RE = re.compile(r"[^\W_]+(?:['’][^\W_]+)?")
_NUMERIC_RE = re.compile(r"^\d[\d,.:]*(?:st|nd|rd|th|s|k|m|am|pm)?$")
_ASSISTANT_OPENERS = (
    "sure", "certainly", "of course", "here is", "here's", "here are", "i'm sorry", "i am sorry",
    "as an ai", "i can't", "i cannot", "i'd be happy", "okay, here", "cleaned:", "transcript:",
)
# Words a cleanup may legitimately introduce (numbers, list words, light grammar).
_ALLOWED_NEW = frozenset(
    """a an the and or but to of in on at for by with is are was were be it its it's this that
    these those i you he she we they me him her us them my your our their not no yes am pm
    a.m p.m dollars dollar percent o'clock first second third one two three four five six
    seven eight nine ten hundred thousand million billion""".split()
)
_NUMBER_WORDS = frozenset(
    """zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen
    fifteen sixteen seventeen eighteen nineteen twenty thirty forty fifty sixty seventy
    eighty ninety hundred thousand million billion half quarter point oh dot""".split()
)


@dataclass(frozen=True)
class GuardResult:
    ok: bool
    text: str
    reason: str = ""


def _words(text: str) -> list[str]:
    return [w.lower().replace("’", "'") for w in _WORD_RE.findall(text)]


def strip_wrappers(text: str) -> str:
    """Remove chatter models sometimes wrap around the answer."""
    text = text.strip()
    text = re.sub(r"^```[a-z]*\n?|\n?```$", "", text).strip()
    text = re.sub(r"(?is)^(?:cleaned(?: text)?|output|clean|result)\s*:\s*", "", text).strip()
    text = re.sub(r"(?is)</?transcript>", "", text).strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'“”":
        text = text[1:-1].strip()
    if text.startswith("“") and text.endswith("”"):
        text = text[1:-1].strip()
    return text


def validate(source: str, candidate: str, *, has_correction: bool) -> GuardResult:
    """Accept ``candidate`` only if it looks like a faithful cleanup of ``source``."""
    text = strip_wrappers(candidate)
    src_words, out_words = _words(source), _words(text)
    if not out_words:
        return GuardResult(False, text, "empty output")
    n_src, n_out = len(src_words), len(out_words)

    lowered = text.lower()
    if lowered.startswith(_ASSISTANT_OPENERS) and not source.lower().startswith(
        lowered.split()[0]
    ):
        return GuardResult(False, text, "assistant-style reply")

    if n_out > n_src * 1.2 + 4:
        return GuardResult(False, text, f"added content ({n_src} -> {n_out} words)")
    min_ratio = 0.2 if has_correction else 0.45
    if n_src >= 6 and n_out < n_src * min_ratio:
        return GuardResult(False, text, f"dropped content ({n_src} -> {n_out} words)")

    src_set = set(src_words)
    src_has_numbers = any(w in _NUMBER_WORDS or w[0].isdigit() for w in src_words)
    novel = [
        w
        for w in out_words
        if w not in src_set
        and w not in _ALLOWED_NEW
        and not (src_has_numbers and (_NUMERIC_RE.match(w) or w in _NUMBER_WORDS))
        and not any(w.startswith(s) or s.startswith(w) for s in src_set if len(s) > 3)
    ]
    if len(novel) > max(2, int(0.15 * n_out)):
        return GuardResult(False, text, f"novel words: {' '.join(novel[:6])}")

    if "?" in source and "?" not in text and not has_correction:
        return GuardResult(False, text, "question mark lost (answered instead of cleaned?)")
    return GuardResult(True, text)


_REWRITE_PREAMBLE_RE = re.compile(
    r"(?is)^(?:(?:sure|certainly|of course|okay|ok)[,!.]?\s*)?"
    r"(?:here(?:'s| is| are)\s+(?:the\s+|your\s+)?[^:\n]{0,60}:\s*)"
)
_REFUSAL_RE = re.compile(
    r"(?i)^(?:i'?m sorry|i am sorry|i can(?:not|'t)|as an ai|i'?m unable|i am unable)"
)


def validate_rewrite(original: str, candidate: str) -> GuardResult:
    """Check and clean a speak-to-edit result; keeps the selection's outer whitespace."""
    text = strip_wrappers(candidate)
    text = strip_wrappers(_REWRITE_PREAMBLE_RE.sub("", text, count=1))
    if not text:
        return GuardResult(False, text, "empty output")
    if _REFUSAL_RE.match(text):
        return GuardResult(False, text, "the model declined the instruction")
    if re.search(r"(?im)^(?:instruction|text)\s*:", text):
        return GuardResult(False, text, "prompt leaked into output")
    if len(text) > 4 * len(original) + 600:
        return GuardResult(False, text, "output far longer than the selection")
    leading = original[: len(original) - len(original.lstrip())]
    trailing = original[len(original.rstrip()) :]
    return GuardResult(True, leading + text + trailing)


def validate_translation(source: str, candidate: str) -> GuardResult:
    """Sanity checks for a translation (word overlap can't be used across languages)."""
    text = strip_wrappers(candidate)
    text = re.sub(r"(?i)^translation\s*(?:\([^)]*\))?\s*:\s*", "", text).strip()
    if not text:
        return GuardResult(False, text, "empty output")
    lowered = text.lower()
    if lowered.startswith(_ASSISTANT_OPENERS) and not source.lower().startswith(
        lowered.split()[0]
    ):
        return GuardResult(False, text, "assistant-style reply")
    if "transcript:" in lowered or "translation (" in lowered:
        return GuardResult(False, text, "prompt leaked into output")
    # Character counts: scripts differ in density (Chinese is ~3x denser than English).
    if len(text) > 3 * len(source) + 40:
        return GuardResult(False, text, f"added content ({len(source)} -> {len(text)} chars)")
    if len(source) > 30 and len(text) < 0.15 * len(source):
        return GuardResult(False, text, f"dropped content ({len(source)} -> {len(text)} chars)")
    return GuardResult(True, text)
