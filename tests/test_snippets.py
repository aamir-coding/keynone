"""Snippets: saved text inserted by voice, on its own or with "insert" mid-dictation."""

from __future__ import annotations

import pytest

from keynone.cleanup import snippets
from keynone.cleanup.pipeline import CleanupPipeline, CleanupSettings
from keynone.config import AppProfile, Config, ConfigError, parse_config
from keynone.engine.session import compose

SAVED = {
    "my email": "jane@example.com",
    "my address": "221B Baker Street\nLondon NW1 6XE",
    "signature": "Best,\nJane",
    "zoom link": "https://example.com/j/123",
}


def config() -> Config:
    return Config(snippets=dict(SAVED))


def say(*fragments: str, profile: AppProfile | None = None) -> str:
    """The text a dictation inserts (smart spacing aside)."""
    cfg = config()
    pipeline = CleanupPipeline(CleanupSettings(llm_mode="off",
                                               snippets=tuple(cfg.snippets.items())))
    for fragment in fragments:
        pipeline.add_sentence(fragment)
    result = pipeline.finish(timeout=2)
    text, _ = compose(result.text, profile or AppProfile(), cfg, spoken_end=result.spoken_end)
    return snippets.fill(text, result.snippets)


@pytest.mark.parametrize("said", [
    "My address.", "my address", "Insert my address.", "Paste my address", "Um, my address.",
    "my adress",  # one recognition slip
])
def test_saying_the_phrase_inserts_the_text_as_written(said: str) -> None:
    assert say(said) == "221B Baker Street\nLondon NW1 6XE"


def test_the_text_is_not_reformatted() -> None:
    assert say("My email.") == "jane@example.com"  # no capital, no period
    assert say("Zoom link") == "https://example.com/j/123"
    # in a chat app too
    assert say("Signature.", profile=AppProfile(style="chat", drop_final_period=True)) == (
        "Best,\nJane")


def test_insert_while_dictating() -> None:
    # the sentence keeps its period after a one-line snippet ...
    assert say("You can reach me at insert my email.") == (
        "You can reach me at jane@example.com.")
    # ... not after one with several lines
    assert say("Thanks for the update.", "Insert signature.") == (
        "Thanks for the update. Best,\nJane")
    assert say("send the invite with insert zoom link and the agenda") == (
        "Send the invite with https://example.com/j/123 and the agenda.")


def test_phrases_are_not_snippets_inside_other_sentences() -> None:
    # Only "insert <phrase>" mid-dictation: "my email" here is ordinary speech.
    assert say("My email is not working today.") == "My email is not working today."
    assert say("Please insert a new page.") == "Please insert a new page."


def test_unknown_or_no_snippets() -> None:
    assert snippets.whole("my address", []) is None
    assert snippets.whole("", SAVED.items()) is None
    assert snippets.mark_inline("insert my address", []) == ("insert my address", {})
    assert snippets.fill("plain text", {}) == "plain text"


def test_placeholders() -> None:
    text, found = snippets.mark_inline("a insert my email b insert zoom link c", SAVED.items())
    assert len(found) == 2 and text.startswith("a ") and text.endswith(" c")
    assert snippets.fill(text, found) == "a jane@example.com b https://example.com/j/123 c"
    later, again = snippets.mark_inline("insert my email", SAVED.items(), used=len(found))
    assert later in again and set(again).isdisjoint(found)  # numbered on across fragments
    assert all(snippets.is_placeholder(c) for c in again)


def test_the_llm_may_not_drop_a_snippet() -> None:
    class Polisher:
        def polish(self, transcript: str, **_: object) -> object:
            class Result:
                truncated = False
                text = "You can reach me at my email."

            return Result()

    pipeline = CleanupPipeline(CleanupSettings(llm_mode="always",
                                               snippets=tuple(SAVED.items())), Polisher())
    pipeline.add_sentence("You can reach me at insert my email.")
    result = pipeline.finish(timeout=5)
    assert snippets.fill(result.text, result.snippets) == "You can reach me at jane@example.com."
    assert result.rejections and not result.llm_accepted


def test_config() -> None:
    text = '[snippets]\n"my email" = "jane@example.com"\n"addr" = """1 Main St\nTown"""\n'
    assert parse_config(text).snippets == {"my email": "jane@example.com",
                                           "addr": "1 Main St\nTown"}
    with pytest.raises(ConfigError, match="empty"):
        parse_config('[snippets]\n"my email" = " "\n')
    with pytest.raises(ConfigError, match="no words"):
        parse_config('[snippets]\n"..." = "x"\n')
