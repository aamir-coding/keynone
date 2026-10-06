"""Guardrails for LLM output.

Small local models occasionally answer a dictated question, follow a dictated
instruction ("translate this ..."), invent words, drop half the text, or "fix"
a correction the wrong way round. The guard aligns the LLM output with its
input word by word and rejects anything that is not a faithful cleanup; the
caller then falls back to the deterministic rule-based text, so a bad
generation can never reach the user.

A faithful cleanup may only delete: filler words, correction cues, repeated
words, and the words a self-correction replaces (just before its cue). It may
not delete anything said after the last correction (the speaker's final
version), add words of its own, or turn a question into a statement.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from keynone.cleanup import rules

_WORD_RE = re.compile(r"[^\W_]+(?:['’][^\W_]+)?")
_NUMERIC_RE = re.compile(r"^\d[\d,.:]*(?:st|nd|rd|th|s|k|m|am|pm)?$")
_ASSISTANT_OPENERS = (
    "sure", "certainly", "of course", "here is", "here's", "here are", "i'm sorry", "i am sorry",
    "as an ai", "i can't", "i cannot", "i'd be happy", "okay, here", "cleaned:", "transcript:",
)
# Words a cleanup may legitimately introduce (numbers, list words, light grammar).
_ALLOWED_NEW = frozenset(
    """a an the and or to of it its it's is are was were be i am pm a.m p.m dollars dollar
    percent o'clock first second third one two three four five six seven eight nine ten
    hundred thousand million billion""".split()
)
# Never added by a faithful cleanup: they change what the sentence says.
_NEVER_NEW = frozenset(
    """but because so therefore however although though since unless yet not never no don't
    didn't won't can't isn't wasn't aren't doesn't""".split()
)
_NUMBER_WORDS = frozenset(
    """zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen
    fifteen sixteen seventeen eighteen nineteen twenty thirty forty fifty sixty seventy
    eighty ninety hundred thousand million billion half quarter point oh dot""".split()
)
# Words a cleanup may delete anywhere: fillers, hedges, discourse markers and the
# words of correction cues.
_DELETABLE = frozenset(
    """um uh er erm ah hmm mm like you know i mean basically literally actually so well okay ok
    yeah yes right kind sort of just really totally honestly anyway anyways oh wait no nope
    sorry scratch strike that this make it rather or correction oops whoops my bad meant
    change to let me rephrase should say also then and the a an
    first second third fourth fifth sixth seventh eighth ninth tenth firstly secondly thirdly
    finally lastly next number step item point bullet""".split()
)
_REPARANDUM_WORDS = 10  # how far before a correction cue words may be dropped


@dataclass(frozen=True)
class GuardResult:
    ok: bool
    text: str
    reason: str = ""


def _words(text: str) -> list[str]:
    return [w.lower().replace("’", "'") for w in _WORD_RE.findall(text)]


def _same(a: str, b: str) -> bool:
    """Same word, allowing inflection ("repetition"/"repetitions")."""
    if a == b:
        return True
    if min(len(a), len(b)) >= 4 and (a.startswith(b) or b.startswith(a)):
        return True
    return len(a) >= 6 and len(b) >= 6 and a[:5] == b[:5]


def _align(src: list[str], out: list[str]) -> tuple[set[int], set[int]]:
    """Longest common subsequence: (aligned source indexes, aligned output indexes)."""
    n, m = len(src), len(out)
    table = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            table[i][j] = (table[i + 1][j + 1] + 1 if _same(src[i], out[j])
                           else max(table[i + 1][j], table[i][j + 1]))
    kept_src: set[int] = set()
    kept_out: set[int] = set()
    i = j = 0
    while i < n and j < m:
        if _same(src[i], out[j]):
            kept_src.add(i)
            kept_out.add(j)
            i += 1
            j += 1
        elif table[i + 1][j] >= table[i][j + 1]:
            i += 1
        else:
            j += 1
    return kept_src, kept_out


def _cue_word_spans(source: str) -> list[tuple[int, int]]:
    """(first, last) word index of each correction cue / restarted phrase in source."""
    spans = []
    for cue in rules.find_cues(source):
        first = len(_words(source[: cue.start]))
        spans.append((first, first + max(1, len(_words(source[cue.start : cue.end])))))
    for m in rules.restart_spans(source):
        first = len(_words(source[: m[0]]))
        spans.append((first, first))
    return sorted(spans)


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


def validate(source: str, candidate: str, *, has_correction: bool,
             strict: bool = False) -> GuardResult:
    """Accept ``candidate`` only if it looks like a faithful cleanup of ``source``.

    ``strict`` (used outside English, where small models are less reliable):
    no invented words (one if the speaker corrected themselves) and no
    statement turned into a question.
    """
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

    if source.count("?") > text.count("?"):
        return GuardResult(False, text, "question mark lost (answered instead of cleaned?)")

    src_set = set(src_words)
    src_has_numbers = any(w in _NUMBER_WORDS or w[0].isdigit() for w in src_words)
    novel = [
        w
        for w in out_words
        if w not in src_set
        and w not in _ALLOWED_NEW
        and not (src_has_numbers and (_NUMERIC_RE.match(w) or w in _NUMBER_WORDS))
        and not (w.isdigit() and int(w) <= 20)  # list numbering
        and not any(_same(w, s) for s in src_set)
    ]
    allowed_novel = (1 if has_correction else 0) if strict else (0 if n_out < 20 else 1)
    if len(novel) > allowed_novel or any(w in _NEVER_NEW for w in novel):
        return GuardResult(False, text, f"novel words: {' '.join(novel[:6])}")

    if strict:
        if "?" in text and "?" not in source:
            return GuardResult(False, text, "statement turned into a question")
    else:
        dropped = _unexplained_drops(source, src_words, out_words)
        if dropped:
            return GuardResult(False, text, f"dropped words: {' '.join(dropped[:6])}")
    return GuardResult(True, text)


def _unexplained_drops(source: str, src_words: list[str], out_words: list[str]) -> list[str]:
    """Source words missing from the output that no cleanup rule explains."""
    kept, _ = _align(src_words, out_words)
    spans = _cue_word_spans(source)
    repair_from = spans[-1][1] if spans else len(src_words)  # the speaker's final version
    output_has_digits = any(w[0].isdigit() for w in out_words)
    dropped = []
    for i, word in enumerate(src_words):
        if i in kept or word in _DELETABLE:
            continue
        if word in _NUMBER_WORDS and output_has_digits:
            continue  # "three thousand" -> "3,000"
        if word in src_words[max(0, i - 3) : i] + src_words[i + 1 : i + 4]:
            continue  # a repetition
        replaced = any(first - _REPARANDUM_WORDS <= i < first for first, _ in spans)
        if replaced and i < repair_from:
            continue  # what a self-correction replaced
        dropped.append(word)
    return dropped


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
