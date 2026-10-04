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

    def recent(self, limit: int = 20) -> list[Dictation]:
        if self._db is None:
            return []
        with self._lock:
            rows = self._db.execute(
                "SELECT id, created_at, app, raw_text, final_text, audio_seconds, latency_ms, "
                "method FROM dictations ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [Dictation(*row) for row in rows]

    def close(self) -> None:
        if self._db is not None:
            with self._lock:
                self._db.close()
                self._db = None
