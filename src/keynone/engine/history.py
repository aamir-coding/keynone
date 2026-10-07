"""Local dictation history (SQLite in data/history.db; never leaves the folder).

Besides the dictations themselves (deleted after ``retention_days``), a ``daily``
table keeps how many dictations, words and seconds of speech there were per day -
counts only, no text - for the statistics on the settings window's home page.
"""

from __future__ import annotations

import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS dictations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at REAL NOT NULL,
    app TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    raw_text TEXT NOT NULL DEFAULT '',
    final_text TEXT NOT NULL DEFAULT '',
    audio_seconds REAL NOT NULL DEFAULT 0,
    latency_ms INTEGER NOT NULL DEFAULT 0,
    llm_calls INTEGER NOT NULL DEFAULT 0,
    method TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_dictations_created ON dictations(created_at);
CREATE TABLE IF NOT EXISTS daily (
    day TEXT PRIMARY KEY,
    dictations INTEGER NOT NULL DEFAULT 0,
    words INTEGER NOT NULL DEFAULT 0,
    audio_seconds REAL NOT NULL DEFAULT 0
);
"""
# Words, and Chinese/Japanese characters one by one (they have no spaces).
_WORDS_RE = re.compile(r"[぀-ヿ㐀-鿿]|[^\W\d_]+(?:['’][^\W\d_]+)*|\d+")


def count_words(text: str) -> int:
    return len(_WORDS_RE.findall(text))


def _day(stamp: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(stamp))


@dataclass(frozen=True)
class Stats:
    """Dictation statistics (speak-to-edit instructions are not counted)."""

    dictations: int = 0
    words: int = 0
    audio_seconds: float = 0.0
    week_dictations: int = 0  # the last 7 days, today included
    week_words: int = 0
    week_audio_seconds: float = 0.0
    days: tuple[tuple[str, int], ...] = ()  # (YYYY-MM-DD, words) for the recent days
    since: str = ""  # the first day with a dictation


@dataclass(frozen=True)
class Dictation:
    id: int
    created_at: float
    app: str
    raw_text: str
    final_text: str
    audio_seconds: float
    latency_ms: int
    method: str


class History:
    def __init__(self, path: Path, *, enabled: bool = True, retention_days: int = 30) -> None:
        self.enabled = enabled
        self._lock = threading.Lock()
        self._db: sqlite3.Connection | None = None
        if not enabled:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(_SCHEMA)
        self._count_older_dictations()
        if retention_days > 0:
            cutoff = time.time() - retention_days * 86400
            self._db.execute("DELETE FROM dictations WHERE created_at < ?", (cutoff,))
        self._db.commit()

    def _count_older_dictations(self) -> None:
        """Statistics for dictations kept from before the daily counts existed (0.2)."""
        assert self._db is not None
        if self._db.execute("SELECT 1 FROM daily LIMIT 1").fetchone() is not None:
            return
        rows = self._db.execute("SELECT created_at, final_text, audio_seconds, method "
                                "FROM dictations").fetchall()
        for created_at, final_text, audio_seconds, method in rows:
            self._count(created_at, final_text, audio_seconds, method)

    def _count(self, created_at: float, final_text: str, audio_seconds: float,
               method: str) -> None:
        assert self._db is not None
        if method == "edit" or not final_text.strip():
            return  # speak-to-edit: the text is a rewrite, not something dictated
        self._db.execute(
            "INSERT INTO daily (day, dictations, words, audio_seconds) VALUES (?, 1, ?, ?) "
            "ON CONFLICT(day) DO UPDATE SET dictations = dictations + 1, "
            "words = words + excluded.words, audio_seconds = audio_seconds + "
            "excluded.audio_seconds",
            (_day(created_at), count_words(final_text), audio_seconds))

    def add(self, *, app: str, title: str, raw_text: str, final_text: str,
            audio_seconds: float, latency_ms: int, llm_calls: int, method: str) -> None:
        if self._db is None:
            return
        now = time.time()
        with self._lock:
            self._db.execute(
                "INSERT INTO dictations (created_at, app, title, raw_text, final_text, "
                "audio_seconds, latency_ms, llm_calls, method) VALUES (?,?,?,?,?,?,?,?,?)",
                (now, app, title, raw_text, final_text, audio_seconds, latency_ms,
                 llm_calls, method),
            )
            self._count(now, final_text, audio_seconds, method)
            self._db.commit()

    def stats(self, days: int = 14, now: float | None = None) -> Stats:
        """Totals, the last 7 days, and words per day for the last ``days`` days."""
        if self._db is None:
            return Stats()
        now = now if now is not None else time.time()
        recent = [_day(now - 86400 * back) for back in range(days - 1, -1, -1)]
        week = recent[-7:]
        with self._lock:
            rows = self._db.execute("SELECT day, dictations, words, audio_seconds FROM daily "
                                    "ORDER BY day").fetchall()
        by_day = {day: (count, words, seconds) for day, count, words, seconds in rows}
        in_week = [by_day[day] for day in week if day in by_day]
        return Stats(
            dictations=sum(r[1] for r in rows), words=sum(r[2] for r in rows),
            audio_seconds=sum(r[3] for r in rows),
            week_dictations=sum(r[0] for r in in_week), week_words=sum(r[1] for r in in_week),
            week_audio_seconds=sum(r[2] for r in in_week),
            days=tuple((day, by_day.get(day, (0, 0, 0.0))[1]) for day in recent),
            since=rows[0][0] if rows else "",
        )

    @staticmethod
    def _where(query: str) -> tuple[str, tuple[str, ...]]:
        """SQL filter for dictations whose text contains ``query`` (any case)."""
        if not query.strip():
            return "", ()
        escaped = query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return " WHERE final_text LIKE ? ESCAPE '\\'", (f"%{escaped}%",)

    def recent(self, limit: int = 20, offset: int = 0, query: str = "") -> list[Dictation]:
        """The newest dictations first; ``query`` keeps those whose text contains it."""
        if self._db is None:
            return []
        where, args = self._where(query)
        with self._lock:
            rows = self._db.execute(
                "SELECT id, created_at, app, raw_text, final_text, audio_seconds, latency_ms, "
                f"method FROM dictations{where} ORDER BY id DESC LIMIT ? OFFSET ?",
                (*args, limit, offset),
            ).fetchall()
        return [Dictation(*row) for row in rows]

    def count(self, query: str = "") -> int:
        if self._db is None:
            return 0
        where, args = self._where(query)
        with self._lock:
            row = self._db.execute(f"SELECT COUNT(*) FROM dictations{where}", args).fetchone()
        return int(row[0])

    def last(self) -> Dictation | None:
        recent = self.recent(1)
        return recent[0] if recent else None

    def latest_id(self) -> int:
        """Changes whenever a dictation is added (cheap to ask often)."""
        if self._db is None:
            return 0
        with self._lock:
            row = self._db.execute("SELECT MAX(id) FROM dictations").fetchone()
        return int(row[0] or 0)

    def delete(self, dictation_id: int) -> None:
        if self._db is None:
            return
        with self._lock:
            self._db.execute("DELETE FROM dictations WHERE id = ?", (dictation_id,))
            self._db.commit()

    def clear(self) -> None:
        """Delete every dictation and the statistics (and shrink the file)."""
        if self._db is None:
            return
        with self._lock:
            self._db.execute("DELETE FROM dictations")
            self._db.execute("DELETE FROM daily")  # the statistics go too
            self._db.commit()
            self._db.execute("VACUUM")

    def close(self) -> None:
        if self._db is not None:
            with self._lock:
                self._db.close()
                self._db = None
