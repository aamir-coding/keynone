"""Learning names and spellings from the user's own corrections.

After a dictation is inserted, the engine reads the text box it went into a few
times during the next minute (UI Automation, never stored). When the user
replaced a word of the dictation with a similar one that looks like a name or a
term ("Amid" -> "Aamir", "github" -> "GitHub", "Chat GPT" -> "ChatGPT"), the pair
is kept in ``data/learned.db`` and applied to later dictations. Other edits -
rewording, grammar, a sentence's capital, numbers - are not learned.

Learned spellings are listed in the settings window (Dictionary) and can be
removed there; ``dictionary.learn = false`` turns learning off.
"""

from __future__ import annotations

import difflib
import re
import sqlite3
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from keynone.cleanup.formatting import lowercase_starter
from keynone.config import AppProfile

_SCHEMA = """
CREATE TABLE IF NOT EXISTS learned (
    heard TEXT PRIMARY KEY,
    written TEXT NOT NULL,
    at_start INTEGER NOT NULL DEFAULT 0,
    exact INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL
);
"""
MAX_PER_DICTATION = 3  # more corrections than that is rewriting, not fixing names


@dataclass(frozen=True)
class Learned:
    heard: str  # what the recognizer wrote
    written: str  # what the user corrected it to
    at_start: bool = False  # learned at a sentence start (also replaced there)
    exact: bool = True  # matched with this case; False: a casing/spacing fix, any case


def learns_in(profile: AppProfile) -> bool:
    """Corrections are watched for in text boxes, not in terminals or for code."""
    return profile.style != "code" and profile.name not in ("terminal", "mintty")


# --------------------------------------------------------------------------- finding


@dataclass(frozen=True)
class _Word:
    text: str  # without the punctuation around it
    sentence_start: bool


_EDGES = "\"'“”‘’()[]{}<>.,;:!?…"


def _words(text: str) -> list[_Word]:
    words = []
    start = True
    previous_end = 0
    for match in re.finditer(r"\S+", text):
        if "\n" in text[previous_end:match.start()]:
            start = True
        core = match.group().strip(_EDGES)
        if core:
            words.append(_Word(core, start))
        start = match.group().rstrip("\"'”’)]}").endswith((".", "!", "?", "…"))
        previous_end = match.end()
    return words


def _distance(a: str, b: str) -> int:
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def _learnable(old: list[_Word], new: list[_Word]) -> Learned | None:
    """A correction that fixes how a name or term was heard, or None."""
    heard = " ".join(w.text for w in old)
    written = " ".join(w.text for w in new)
    if heard == written or len(written) > 40:
        return None
    heard_key = re.sub(r"[\W_]", "", heard.lower())
    written_key = re.sub(r"[\W_]", "", written.lower())
    if not heard_key or not written_key or heard_key.isdigit() or written_key.isdigit():
        return None  # punctuation, or a number changed: not a spelling
    at_start = old[0].sentence_start
    if heard_key == written_key:  # only capitals or spaces changed
        capitals = sum(c.isupper() for c in written) - sum(c.isupper() for c in heard)
        if " " in heard and " " not in written:  # "Chat GPT" -> "ChatGPT"
            return Learned(heard, written, at_start, exact=False)
        if capitals <= 0 or (at_start and written[1:] == heard[1:]):
            return None  # fewer capitals, or only a sentence's first letter: style
        return Learned(heard, written, at_start, exact=False)
    similarity = 1 - _distance(heard_key, written_key) / max(len(heard_key), len(written_key))
    name_like = any(c.isupper() for c in written) or any(c.isdigit() for c in written)
    if similarity < 0.5 or not name_like or lowercase_starter(written.lower()):
        return None  # another word, or grammar ("their" -> "there"), not a name
    if heard == heard.lower() and similarity < 0.75:
        return None  # a lowercase word heard for a name: too likely a real word elsewhere
    return Learned(heard, written, at_start, exact=True)


def corrections(inserted: str, current: str) -> list[Learned]:
    """Name and spelling fixes the user made to ``inserted`` (a dictation as it was
    typed) in ``current`` (the text box's text now)."""
    inserted = inserted.strip()
    if not inserted or inserted in current:
        return []
    before, after = _words(inserted), _words(current)
    if not before or not after:
        return []
    keys_a, keys_b = [w.text for w in before], [w.text for w in after]
    blocks = [b for b in difflib.SequenceMatcher(None, keys_a, keys_b, autojunk=False)
              .get_matching_blocks() if b.size]
    if sum(b.size for b in blocks) < max(2, 0.6 * len(before)):
        return []  # the dictation isn't there any more (sent, deleted or rewritten)
    # The part of the text box that holds the dictation, corrected words at its ends too.
    first, last = blocks[0], blocks[-1]
    start = max(0, first.b - first.a)
    end = min(len(after), last.b + last.size + (len(before) - last.a - last.size))
    if end - start > 2 * len(before) + 6:
        return []  # scattered matches in a long text: no reliable alignment
    window = after[start:end]
    found: list[Learned] = []
    matcher = difflib.SequenceMatcher(None, keys_a, [w.text for w in window], autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "replace" and 1 <= i2 - i1 <= 3 and 1 <= j2 - j1 <= 3:
            learned = _learnable(before[i1:i2], window[j1:j2])
            if learned is not None:
                found.append(learned)
    return found if len(found) <= MAX_PER_DICTATION else []


# --------------------------------------------------------------------------- applying


def _at_sentence_start(text: str, index: int) -> bool:
    before = text[:index].rstrip(" \t\"'“‘(")
    return not before or before.endswith((".", "!", "?", "…", "\n"))


def apply_learned(text: str, learned: Iterable[Learned]) -> str:
    """Learned spellings in a dictation. An exact one only where the recognizer wrote
    the same thing, and at a sentence start only if it was learned there ("Amid the
    noise" stays); a casing or spacing fix in any case ("chat gpt" -> "ChatGPT")."""
    for item in learned:
        words = [re.escape(w) for w in item.heard.split()]
        if not words:
            continue
        if item.exact:
            pattern = re.compile(r"(?<![\w'’-])" + r"\s+".join(words) + r"(?![\w'’-])")

            def exact(match: re.Match[str], item: Learned = item) -> str:
                if not item.at_start and _at_sentence_start(match.string, match.start()):
                    return match.group(0)
                return item.written

            text = pattern.sub(exact, text)
        else:
            pattern = re.compile(r"(?i)(?<![\w'’-])" + r"[\s-]*".join(words) + r"(?![\w'’-])")

            def anycase(_match: re.Match[str], item: Learned = item) -> str:
                return item.written

            text = pattern.sub(anycase, text)
    return text


# --------------------------------------------------------------------------- storing


class LearnedStore:
    """``data/learned.db``: what was learned (written by the engine, edited by the
    settings window)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._db: sqlite3.Connection | None = None

    def _open(self) -> sqlite3.Connection:
        if self._db is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(str(self.path), check_same_thread=False)
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.executescript(_SCHEMA)
        return self._db

    def entries(self) -> list[Learned]:
        """Everything learned, newest first (nothing when there is no file yet)."""
        if self._db is None and not self.path.exists():
            return []
        with self._lock:
            rows = self._open().execute("SELECT heard, written, at_start, exact FROM learned "
                                        "ORDER BY created_at DESC, rowid DESC").fetchall()
        return [Learned(heard, written, bool(start), bool(exact))
                for heard, written, start, exact in rows]

    def add(self, item: Learned) -> bool:
        """Keep ``item`` (a later correction of the same words replaces an earlier one);
        False if it was known already."""
        with self._lock:
            db = self._open()
            row = db.execute("SELECT written, at_start FROM learned WHERE heard = ?",
                             (item.heard,)).fetchone()
            if row is not None and row[0] == item.written and (row[1] or not item.at_start):
                return False
            db.execute("INSERT OR REPLACE INTO learned (heard, written, at_start, exact, "
                       "created_at) VALUES (?, ?, ?, ?, ?)",
                       (item.heard, item.written, int(item.at_start or bool(row and row[1])),
                        int(item.exact), time.time()))
            db.commit()
        return True

    def remove(self, heard: str) -> None:
        if self._db is None and not self.path.exists():
            return
        with self._lock:
            db = self._open()
            db.execute("DELETE FROM learned WHERE heard = ?", (heard,))
            db.commit()

    def clear(self) -> None:
        if self._db is None and not self.path.exists():
            return
        with self._lock:
            db = self._open()
            db.execute("DELETE FROM learned")
            db.commit()

    def close(self) -> None:
        with self._lock:
            if self._db is not None:
                self._db.close()
                self._db = None
