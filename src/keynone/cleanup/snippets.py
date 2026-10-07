"""Snippets: saved text inserted by voice (``[snippets]`` in config.toml).

Say the phrase on its own ("my address") and its text is inserted exactly as
saved. Inside a longer dictation, say "insert" first ("you can reach me at
insert my email"): the phrase is swapped for a placeholder that the cleanup
leaves alone, and the saved text goes in at the very end, after all the
formatting, so nothing in it is re-capitalized, re-punctuated or "polished".
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from keynone.cleanup.rules import words_match

# Unicode private-use characters: the recognizer never writes them.
_FIRST_PLACEHOLDER = 0xE000
_LAST_PLACEHOLDER = 0xE0FF
PLACEHOLDERS = f"{chr(_FIRST_PLACEHOLDER)}-{chr(_LAST_PLACEHOLDER)}"
# Said before the phrase of a dictation that is only a snippet: "insert my address".
_LEADS = ("insert", "paste", "type")
_WORD_RE = re.compile(r"[^\W_]+(?:['’][^\W_]+)*")

Snippets = Iterable[tuple[str, str]]  # (phrase, text)


def normalize(text: str) -> str:
    """ "My e-mail." -> "my e mail": lowercase words, punctuation dropped."""
    return " ".join(w.lower().replace("’", "'") for w in _WORD_RE.findall(text))


def is_placeholder(char: str) -> bool:
    return len(char) == 1 and _FIRST_PLACEHOLDER <= ord(char) <= _LAST_PLACEHOLDER


def _matches(spoken: str, phrase: str) -> bool:
    """Same words, allowing one recognition slip in words of 4+ letters."""
    return spoken == phrase or words_match(spoken, phrase)


def whole(dictation: str, snippets: Snippets) -> str | None:
    """The text of the snippet a whole dictation names ("my address", "insert my
    address"), or None."""
    said = normalize(dictation)
    if not said:
        return None
    first, _, rest = said.partition(" ")
    for phrase, text in snippets:
        wanted = normalize(phrase)
        if wanted and (_matches(said, wanted) or (first in _LEADS and _matches(rest, wanted))):
            return text
    return None


def mark_inline(text: str, snippets: Snippets, used: int = 0) -> tuple[str, dict[str, str]]:
    """Swap every "insert <phrase>" in ``text`` for a placeholder.

    ``used`` is how many placeholders the dictation has already. Returns the text
    and {placeholder: snippet text}."""
    phrases = [(normalize(phrase).split(), saved) for phrase, saved in snippets]
    phrases = [(words, saved) for words, saved in phrases if words]
    if not phrases or "insert" not in text.lower():
        return text, {}
    words = list(_WORD_RE.finditer(text))
    found: dict[str, str] = {}
    out, position, i = [], 0, 0
    while i < len(words):
        if words[i].group(0).lower() != "insert":
            i += 1
            continue
        for wanted, saved in sorted(phrases, key=lambda p: -len(p[0])):
            span = words[i + 1:i + 1 + len(wanted)]
            if len(span) == len(wanted) and _matches(
                    " ".join(normalize(w.group(0)) for w in span), " ".join(wanted)):
                index = _FIRST_PLACEHOLDER + used + len(found)
                if index > _LAST_PLACEHOLDER:
                    break
                placeholder = chr(index)
                found[placeholder] = saved
                out.append(text[position:words[i].start()] + placeholder)
                position = span[-1].end()
                i += 1 + len(wanted)
                break
        else:
            i += 1
    if not found:
        return text, {}
    out.append(text[position:])
    return "".join(out), found


def fill(text: str, placeholders: dict[str, str]) -> str:
    """Put the saved text where the placeholders are (and drop unknown ones).

    A sentence's period right after a saved text that has several lines or its own
    final punctuation goes ("... insert signature." -> "Best,\\nJane", not "Jane.")."""
    if not any(is_placeholder(c) for c in text):
        return text
    out: list[str] = []
    skip_period = False
    for char in text:
        if is_placeholder(char):
            saved = placeholders.get(char, "")
            out.append(saved)
            skip_period = "\n" in saved.strip() or saved.rstrip()[-1:] in (".", "!", "?")
            continue
        if skip_period and char in ".!?":
            skip_period = False
            continue
        skip_period = False
        out.append(char)
    return "".join(out)
