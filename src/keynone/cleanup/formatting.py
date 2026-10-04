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


def join_units(units: Iterable[str]) -> str:
    out = ""
    for unit in units:
        if not unit:
            continue
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
