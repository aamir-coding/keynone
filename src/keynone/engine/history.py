"""Local dictation history (SQLite in data/history.db; never leaves the folder)."""

from __future__ import annotations

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
"""


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
        if retention_days > 0:
            cutoff = time.time() - retention_days * 86400
            self._db.execute("DELETE FROM dictations WHERE created_at < ?", (cutoff,))
        self._db.commit()

    def add(self, *, app: str, title: str, raw_text: str, final_text: str,
            audio_seconds: float, latency_ms: int, llm_calls: int, method: str) -> None:
        if self._db is None:
            return
        with self._lock:
            self._db.execute(
                "INSERT INTO dictations (created_at, app, title, raw_text, final_text, "
                "audio_seconds, latency_ms, llm_calls, method) VALUES (?,?,?,?,?,?,?,?,?)",
                (time.time(), app, title, raw_text, final_text, audio_seconds, latency_ms,
                 llm_calls, method),
            )
            self._db.commit()

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
        """Delete every dictation (and shrink the file)."""
        if self._db is None:
            return
        with self._lock:
            self._db.execute("DELETE FROM dictations")
            self._db.commit()
            self._db.execute("VACUUM")

    def close(self) -> None:
        if self._db is not None:
            with self._lock:
                self._db.close()
                self._db = None
