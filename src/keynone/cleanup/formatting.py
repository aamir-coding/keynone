"""Final layout: joining polished units, list normalization, per-app touches."""

from __future__ import annotations

import re
from collections.abc import Iterable

from keynone.config import Style

_NUMBERED_RE = re.compile(r"^(\s*)(\d+)[.)]\s+(.*)$")
_BULLET_RE = re.compile(r"^(\s*)[-*•–]\s+(.*)$")


def is_list_line(line: str) -> bool:
    return bool(_NUMBERED_RE.match(line) or _BULLET_RE.match(line))


def normalize_lists(text: str) -> str:
    """Renumber numbered blocks from 1 and use ``- `` for bullets."""
    out: list[str] = []
    counter = 0
    for line in text.split("\n"):
        numbered = _NUMBERED_RE.match(line)
        bullet = _BULLET_RE.match(line)
        if numbered:
            counter += 1
            out.append(f"{numbered.group(1)}{counter}. {numbered.group(3)}")
            continue
        counter = 0
        out.append(f"{bullet.group(1)}- {bullet.group(2)}" if bullet else line)
    return "\n".join(out)


_SENTENCE_END_RE = re.compile(r"[.!?…:;,][\"'”’)\]]*$")
_SUBJECTS = r"(?:you|we|i|they|he|she|anyone|someone|somebody|anybody|everyone)"
_QUESTION_START_RE = re.compile(
    r"(?i)^(?:"
    r"(?:what(?!\s+an?\b)|why|how|where|when|who|whom|whose|which)\b"
    # "do"/"have" are also imperatives ("Do it now", "Have a look"): subjects only
    rf"|(?:do|have)\s+{_SUBJECTS}\b"
    r"|(?:can|could|would|will|shall|should|may|might|does|did|is|are|was|were|has|had"
    r"|isn't|aren't|wasn't|don't|doesn't|didn't|won't|wouldn't|can't|couldn't|shouldn't)"
    rf"\s+(?:{_SUBJECTS}|it|this|that|there|the|my|your|our|their)\b)"
)


def _last_sentence(line: str) -> str:
    parts = re.split(r"(?<=[.!?…])\s+", line.strip())
    return parts[-1] if parts else line


def _close_sentence(text: str) -> str:
    """End an unpunctuated sentence with "?" if it reads as a question, else "."."""
    last_line = text.rstrip().rsplit("\n", 1)[-1]
    if not last_line.strip() or is_list_line(last_line) or _SENTENCE_END_RE.search(last_line):
        return text
    mark = "?" if _QUESTION_START_RE.match(_last_sentence(last_line)) else "."
    return text.rstrip() + mark


def join_units(units: Iterable[str]) -> str:
    """Join cleaned units; each unit is a separate sentence (or list/paragraph)."""
    out = ""
    for unit in units:
        if not unit:
            continue
        if out and not out.endswith("\n") and not unit.startswith("\n"):
            out = _close_sentence(out)
        if not out or out.endswith("\n") or unit.startswith("\n"):
            out += unit
        elif is_list_line(out.rsplit("\n", 1)[-1]) or is_list_line(unit.split("\n", 1)[0]):
            out += "\n" + unit
        else:
            out += " " + unit
    return out


def finalize(
    text: str, *, style: Style, drop_final_period: bool, trailing_space: bool
) -> str:
    """Last touches before insertion into the target app."""
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        return ""
    text = normalize_lists(text)
    if style != "code":
        text = _close_sentence(text)
    if (
        style == "chat"
        and drop_final_period
        and "\n" not in text
        and text.endswith(".")
        and not text.endswith("..")
        and not re.search(r"[.!?…]\s", text[:-1])
    ):
        text = text[:-1]
    if trailing_space and not text.endswith("\n"):
        text += " "
    return text
