"""The dictation history (data/history.db): what the History page and paste-last use."""

from __future__ import annotations

import time
from pathlib import Path

from keynone.engine.history import Dictation, History
from keynone.settings.history_view import details, shorten, when


def _history(tmp_path: Path, *texts: str) -> History:
    history = History(tmp_path / "history.db")
    for text in texts:
        history.add(app="notepad.exe", title="", raw_text=text, final_text=text,
                    audio_seconds=2.5, latency_ms=300, llm_calls=0, method="paste")
    return history


def test_search_paging_and_counts(tmp_path: Path) -> None:
    history = _history(tmp_path, "Hello world.", "100% done", "file_name here", "Goodbye.")
    assert [d.final_text for d in history.recent(2)] == ["Goodbye.", "file_name here"]
    assert [d.final_text for d in history.recent(2, offset=2)] == ["100% done", "Hello world."]
    assert [d.final_text for d in history.recent(10, query="HELLO")] == ["Hello world."]
    # % and _ are searched for, not wildcards
    assert [d.final_text for d in history.recent(10, query="%")] == ["100% done"]
    assert [d.final_text for d in history.recent(10, query="_")] == ["file_name here"]
    assert history.count() == 4 and history.count("o") == 3
    last = history.last()
    assert last is not None and last.final_text == "Goodbye."


def test_delete_clear_and_change_detection(tmp_path: Path) -> None:
    history = _history(tmp_path, "one", "two")
    first = history.latest_id()
    newest = history.last()
    assert newest is not None
    history.delete(newest.id)
    assert [d.final_text for d in history.recent()] == ["one"]
    assert history.latest_id() != first
    history.clear()
    assert history.count() == 0 and history.last() is None
    history.close()


def test_off_history_reads_as_empty(tmp_path: Path) -> None:
    history = History(tmp_path / "history.db", enabled=False)
    assert history.recent() == [] and history.count() == 0 and history.last() is None
    assert not (tmp_path / "history.db").exists()


def test_list_texts() -> None:
    now = time.mktime((2026, 10, 7, 15, 0, 0, 0, 0, -1))
    assert when(now - 600, now) == "Today 14:50"
    assert when(now - 86400, now).startswith("Yesterday ")
    assert when(time.mktime((2026, 10, 3, 18, 2, 0, 0, 0, -1)), now) == "Sat 3 Oct, 18:02"
    assert when(time.mktime((2025, 10, 3, 18, 2, 0, 0, 0, -1)), now) == "3 Oct 2025, 18:02"
    item = Dictation(1, now - 60, "notepad.exe", "", "Hi.", 3.25, 200, "paste")
    assert details(item, now) == "Today 14:59 · notepad · 3.2 s"
    edited = Dictation(2, now - 60, "", "", "Hi.", 12.4, 200, "edit")
    assert details(edited, now) == "Today 14:59 · 12 s · speak-to-edit"
    assert shorten("a  b\nc") == "a b c"
    assert len(shorten("word " * 200)) < 300 and shorten("word " * 200).endswith("…")


# --------------------------------------------------------------------------- statistics


def test_daily_statistics(tmp_path: Path) -> None:
    from keynone.engine.history import count_words

    history = _history(tmp_path, "One two three.", "Four five.")
    history.add(app="", title="", raw_text="shorter", final_text="A rewritten selection.",
                audio_seconds=1.0, latency_ms=0, llm_calls=1, method="edit")  # not dictated
    stats = history.stats(days=14)
    assert (stats.dictations, stats.words, stats.audio_seconds) == (2, 5, 5.0)
    assert (stats.week_dictations, stats.week_words) == (2, 5)
    assert len(stats.days) == 14 and stats.days[-1][1] == 5 and stats.days[0][1] == 0
    assert stats.since == stats.days[-1][0]
    # deleting one dictation keeps the counts; clearing the history resets them
    newest = history.last()
    assert newest is not None
    history.delete(newest.id)
    assert history.stats().words == 5
    history.clear()
    assert history.stats().dictations == 0
    assert count_words("Don't stop: 12 apples") == 4
    assert count_words("你好世界 hello") == 5


def test_statistics_count_dictations_kept_from_before(tmp_path: Path) -> None:
    import sqlite3

    db = tmp_path / "history.db"
    old = sqlite3.connect(db)  # a 0.2 history: dictations, no daily counts
    old.execute("CREATE TABLE dictations (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at "
                "REAL NOT NULL, app TEXT NOT NULL DEFAULT '', title TEXT NOT NULL DEFAULT '', "
                "raw_text TEXT NOT NULL DEFAULT '', final_text TEXT NOT NULL DEFAULT '', "
                "audio_seconds REAL NOT NULL DEFAULT 0, latency_ms INTEGER NOT NULL DEFAULT 0, "
                "llm_calls INTEGER NOT NULL DEFAULT 0, method TEXT NOT NULL DEFAULT '')")
    old.execute("INSERT INTO dictations (created_at, final_text, audio_seconds, method) "
                "VALUES (?, 'Hello there.', 2.0, 'paste')", (time.time() - 86400 * 3,))
    old.commit()
    old.close()
    history = History(db)
    stats = history.stats()
    assert (stats.dictations, stats.words) == (1, 2)
    assert stats.week_words == 2 and stats.days[-4][1] == 2
    history.close()
    again = History(db)  # counted once, not on every start
    assert again.stats().dictations == 1
    again.close()
