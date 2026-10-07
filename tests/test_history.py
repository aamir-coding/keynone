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
