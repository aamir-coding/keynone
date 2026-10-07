"""Learning names and spellings from the user's own corrections."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from keynone.cleanup.pipeline import CleanupPipeline, CleanupSettings
from keynone.config import AppProfile
from keynone.engine.learn_watch import CorrectionWatcher
from keynone.learning import Learned, LearnedStore, apply_learned, corrections, learns_in
from keynone.listener.main import learned_message


@pytest.mark.parametrize(("inserted", "now", "learned"), [
    ("Please ask Amid to review the draft.",
     "Earlier text. Please ask Aamir to review the draft. Later text.",
     [Learned("Amid", "Aamir")]),
    ("We use github for the code.", "We use GitHub for the code.",
     [Learned("github", "GitHub", exact=False)]),
    ("Ask Chat GPT about it.", "Ask ChatGPT about it.",
     [Learned("Chat GPT", "ChatGPT", exact=False)]),
    ("Sara will join.", "Sarah will join.", [Learned("Sara", "Sarah", at_start=True)]),
    ("I met keynon users.", "I met Keynone users.", [Learned("keynon", "Keynone")]),
])
def test_name_and_spelling_fixes_are_found(inserted: str, now: str,
                                           learned: list[Learned]) -> None:
    assert corrections(inserted, now) == learned


@pytest.mark.parametrize(("inserted", "now"), [
    ("Meet on Monday at noon.", "Meet on Tuesday at noon."),  # a different word
    ("It costs 15 dollars.", "It costs 50 dollars."),  # a number
    ("I sent there report today.", "I sent their report today."),  # grammar
    ("Their going home now.", "They're going home now."),  # grammar, sentence start
    ("the plan is ready.", "The plan is ready."),  # a sentence's capital
    ("We should go now.", "We should go now."),  # unchanged
    ("Hello there my friend.", "Something else entirely."),  # gone
    ("Ask Amid about it.", "Ask Amid about it, and Bob too."),  # only added words
    ("one two three four five six", "uno dos tres cuatro five six"),  # rewritten
    ("He said amid the noise.", "He said Aamir the noise."),  # a lowercase real word
])
def test_other_edits_are_not_learned(inserted: str, now: str) -> None:
    assert corrections(inserted, now) == []


def test_learned_spellings_are_applied() -> None:
    name = Learned("Amid", "Aamir")
    assert apply_learned("Please ask Amid to call.", [name]) == "Please ask Aamir to call."
    assert apply_learned("Amid the noise, we left.", [name]) == "Amid the noise, we left."
    assert apply_learned("Amid will call.", [Learned("Amid", "Aamir", at_start=True)]) == (
        "Aamir will call.")
    assert apply_learned("Walk amid trees.", [name]) == "Walk amid trees."  # exact case only
    casing = Learned("github", "GitHub", exact=False)
    assert apply_learned("use github or Github", [casing]) == "use GitHub or GitHub"
    spacing = Learned("Chat GPT", "ChatGPT", exact=False)
    assert apply_learned("ask chat gpt or chat-gpt", [spacing]) == "ask ChatGPT or ChatGPT"


def test_the_pipeline_uses_them() -> None:
    settings = CleanupSettings(llm_mode="off", learned=(Learned("Amid", "Aamir"),))
    pipeline = CleanupPipeline(settings)
    pipeline.add_sentence("Please ask Amid to call me.")
    assert pipeline.finish(timeout=2).text == "Please ask Aamir to call me."


def test_store(tmp_path: Path) -> None:
    store = LearnedStore(tmp_path / "learned.db")
    assert store.entries() == [] and not (tmp_path / "learned.db").exists()
    assert store.add(Learned("Amid", "Aamir"))
    assert not store.add(Learned("Amid", "Aamir"))  # known already
    assert store.add(Learned("github", "GitHub", exact=False))
    assert [e.written for e in store.entries()] == ["GitHub", "Aamir"]  # newest first
    assert store.add(Learned("Amid", "Amir"))  # corrected differently later: replaced
    assert {e.heard: e.written for e in store.entries()} == {"Amid": "Amir", "github": "GitHub"}
    store.remove("Amid")
    assert [e.heard for e in store.entries()] == ["github"]
    store.clear()
    assert store.entries() == []
    store.close()


def test_where_it_learns() -> None:
    assert learns_in(AppProfile(name="default"))
    assert learns_in(AppProfile(name="chat", style="chat"))
    assert not learns_in(AppProfile(name="code", style="code"))
    assert not learns_in(AppProfile(name="terminal", style="prompt"))


class _FakeUia:
    def __init__(self, texts: list[str | None]) -> None:
        self.texts = texts
        self.unwatched: list[int] = []

    def watch_focused(self, *, timeout: float = 0.5) -> int | None:
        return 7

    def watched_text(self, handle: int, *, timeout: float = 0.5,
                     max_chars: int = 100_000) -> str | None:
        return self.texts.pop(0) if self.texts else None

    def unwatch(self, handle: int) -> None:
        self.unwatched.append(handle)


def test_the_watcher_learns_a_correction_once_it_is_finished(tmp_path: Path) -> None:
    inserted = "Please ask Amid to review it."
    uia = _FakeUia([inserted, "Please ask Aami to review it.",  # still typing
                    "Please ask Aamir to review it.", "Please ask Aamir to review it."])
    store = LearnedStore(tmp_path / "learned.db")
    done = threading.Event()
    reported: list[list[Learned]] = []

    def learned(items: list[Learned]) -> None:
        reported.append(items)
        done.set()

    watcher = CorrectionWatcher(uia, store, learned)
    watcher.watch(inserted, checks=(0.0, 0.01, 0.02, 0.03))
    assert done.wait(5)
    assert reported == [[Learned("Amid", "Aamir")]]
    assert [e.written for e in store.entries()] == ["Aamir"]  # not "Aami"
    for _ in range(100):  # the watcher lets go of the text box at the end
        if uia.unwatched:
            break
        threading.Event().wait(0.01)
    assert uia.unwatched == [7]
    store.close()


def test_the_tray_message() -> None:
    assert learned_message([{"heard": "Amid", "written": "Aamir"}]).startswith(
        "“Aamir” from now on (it heard “Amid”).")
    assert "“A”, “B”" in learned_message(
        [{"heard": "a", "written": "A"}, {"heard": "b", "written": "B"}])
