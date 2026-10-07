"""Spoken punctuation and spoken addresses: when a word is a mark, and when it stays."""

from __future__ import annotations

from collections import Counter

import pytest

from keynone.cleanup.formatting import finalize
from keynone.cleanup.pipeline import CleanupPipeline, CleanupSettings
from keynone.cleanup.spoken import convert, missing_mark, open_quote


def text(spoken: str, style: str = "default") -> str:
    return convert(spoken, style=style).text


@pytest.mark.parametrize(("said", "written"), [
    ("Dear John comma I hope you are well period see you soon exclamation mark",
     "Dear John, I hope you are well. See you soon!"),
    # the recognizer's own punctuation next to a spoken mark gives way to it
    ("Dear John Comma, I hope you are well period. See you soon exclamation mark",
     "Dear John, I hope you are well. See you soon!"),
    ("Hello Comma How are you", "Hello, how are you"),
    ("Is it ready question mark", "Is it ready?"),
    ("We need three things colon milk comma eggs and bread",
     "We need three things: milk, eggs and bread"),
    ("The meeting is at five semicolon bring your laptop",
     "The meeting is at five; bring your laptop"),
    ("Call me open bracket after five close bracket tomorrow", "Call me (after five) tomorrow"),
    ("Call me open paren after five closed paren", "Call me (after five)"),
    ("the list open square bracket one close square bracket", "the list [one]"),
    ("He said quote I am done end quote and left", 'He said "I am done" and left'),
    ("She wrote open quote see you close quote", 'She wrote "see you"'),
    ("wait dot dot dot what", "wait… what"),
    ("well hyphen known", "well-known"),
    ("and slash or", "and/or"),
    ("Figma slash Miro boards", "Figma/Miro boards"),
    ("Johnson ampersand Johnson", "Johnson & Johnson"),
    ("I love it dash really", "I love it – really"),
    ("follow at sign keynone", "follow @keynone"),
    ("Time colon three PM", "Time: three PM"),
])
def test_spoken_marks(said: str, written: str) -> None:
    assert text(said) == written


@pytest.mark.parametrize("said", [
    "Add a comma after the name",
    "The Oxford comma is optional",
    "use comma separated values",
    "Over a period of three weeks the colon heals",
    "The trial period ends Friday",
    "The payment period ends Friday",
    "There is a question mark over his future",
    "Put the text in brackets",
    "Commas and periods are punctuation",
    "colon cancer screening saves lives",
    "I had to dash to the store",
    "a dash of salt",
    "we'll dash off a reply",
    "we need to slash costs",
    "slash the prices",
    "get a quote from them",
    "quote me on that",
    "quote unquote busy",
    "the word comma is long",
    "type comma here",
    "an open bracket",
    "the end quote",
])
def test_words_that_stay_words(said: str) -> None:
    assert text(said) == said


def test_literal_types_the_word() -> None:
    assert text("literal comma is a word") == "comma is a word"
    assert text("type literal period please") == "type period please"
    assert text("say literal question mark") == "say question mark"


@pytest.mark.parametrize(("said", "written"), [
    ("My email is Amid dot K at Gmail dot com.", "My email is amid.k@gmail.com."),
    ("jane dot doe at example dot com", "jane.doe@example.com"),
    ("write to jane underscore doe at example dot co dot uk", "write to jane_doe@example.co.uk"),
    ("mail sales at the rate example dot in", "mail sales@example.in"),
    ("reach me at jane at example dot com", "reach me at jane@example.com"),
    ("visit github dot com slash keynone for details", "visit github.com/keynone for details"),
    ("go to www dot example dot org", "go to www.example.org"),
    ("https colon slash slash example dot com slash pricing", "https://example.com/pricing"),
    ("I'm at google dot com", "I'm at google.com"),
    ("open main dot py and config dot toml", "open main.py and config.toml"),
    ("check readme dot MD", "check readme.md"),
])
def test_spoken_addresses(said: str, written: str) -> None:
    assert text(said) == written


@pytest.mark.parametrize("said", [
    "email me at work dot com",  # "me" is no address
    "It's a dot com company",
    "polka dot dress",
    "we met at the rate of ten an hour",
    "on the dot",
])
def test_addresses_need_an_address(said: str) -> None:
    assert text(said) == said


def test_code_and_prompt_styles() -> None:
    assert text("git commit dash m fix typo", "prompt") == "git commit -m fix typo"
    assert text("git log dash dash oneline", "code") == "git log --oneline"
    assert text("print user dot name", "code") == "print user.name"
    assert text("print user dot name") == "print user dot name"  # prose: no member access
    assert text("I love it dash really") == "I love it – really"


def test_a_mark_at_the_start_belongs_to_what_came_before() -> None:
    converted = convert("period see you soon")
    assert converted.text == ". See you soon" and converted.lead == "."
    alone = convert("question mark")
    assert alone.text == "?" and alone.lead == "?" and alone.ends_with_mark
    assert convert("comma").lead == ","
    assert convert("Over a", before="").text == "Over a"
    # "Over a [pause] period of time": the word before the pause still counts
    assert convert("period of time", before="Over a").text == "period of time"


def test_missing_quote_opening_is_added_after_the_verb() -> None:
    assert text("He said, I am done end quote and left") == 'He said, "I am done" and left'
    assert open_quote('He told me, I am done"') == 'He told me, "I am done"'
    assert open_quote('I am done"') == 'I am done"'  # nothing to anchor it: left alone


def test_marks_are_counted_for_the_guard() -> None:
    converted = convert("Hi comma see you semicolon later exclamation mark")
    assert converted.marks == Counter({",": 1, ";": 1, "!": 1})
    marks = converted.marks
    assert missing_mark(marks, "Hi, see you; later!", "Hi, see you later!") == ";"
    assert missing_mark(marks, "Hi, see you; later!", "Hi see you; later!") == ""  # commas: LLM's
    assert missing_mark(marks, "Hi see you later!", "Hi see you later!") == ""


# --------------------------------------------------------------------------- pipeline


def dictate(*fragments: str, style: str = "default", chat: bool = False,
            spoken: bool = True) -> str:
    pipeline = CleanupPipeline(CleanupSettings(llm_mode="off", style=style,
                                               spoken_punctuation=spoken))
    for fragment in fragments:
        pipeline.add_sentence(fragment)
    result = pipeline.finish(timeout=2)
    return finalize(result.text, style="chat" if chat else style,
                    drop_final_period=chat and not result.spoken_end, trailing_space=False)


def test_pipeline_with_pauses() -> None:
    assert dictate("Dear John Comma, I hope you are well period.",
                   "See you soon exclamation mark") == (
        "Dear John, I hope you are well. See you soon!")
    assert dictate("I hope you are well,", "period", "see you soon") == (
        "I hope you are well. See you soon.")
    assert dictate("Call me open bracket", "after five close bracket tomorrow") == (
        "Call me (after five) tomorrow.")
    assert dictate("He said, I am done", "end quote and left") == 'He said, "I am done" and left.'
    assert dictate("Over a", "period of three weeks") == "Over a period of three weeks."


def test_a_dictated_colon_and_commas_keep_the_list_inline() -> None:
    assert dictate("We need three things colon milk comma eggs and bread") == (
        "We need three things: milk, eggs and bread.")
    # the recognizer's own punctuation: still a list, as before
    assert dictate("We need three things: milk, eggs and bread.") == (
        "We need three things:\n1. Milk\n2. Eggs\n3. Bread")


def test_a_dictation_that_is_only_a_mark() -> None:
    assert dictate("question mark") == "?"
    assert dictate("Comma.") == ","
    assert dictate("open bracket") == "("


def test_a_dictation_that_starts_with_a_mark() -> None:
    assert dictate("comma and then we went home") == ", and then we went home."
    assert dictate("period", "Thanks for the update") == ". Thanks for the update."


def test_a_spoken_final_period_stays_in_chat_apps() -> None:
    assert dictate("see you soon period", chat=True) == "See you soon."
    assert dictate("see you soon", chat=True) == "See you soon"


def test_switched_off() -> None:
    assert dictate("Dear John comma hi", spoken=False) == "Dear John comma hi."


def test_other_languages_are_left_alone() -> None:
    pipeline = CleanupPipeline(CleanupSettings(llm_mode="off", language="auto"))
    pipeline.add_sentence("Hola Juan coma cómo estás")
    assert "coma" in pipeline.finish(timeout=2).text


class _Polisher:
    def __init__(self, text: str) -> None:
        self.text = text

    def polish(self, transcript: str, **_: object) -> object:
        class Result:
            truncated = False

        result = Result()
        result.text = self.text  # type: ignore[attr-defined]
        return result


def test_the_llm_may_not_drop_a_spoken_mark() -> None:
    # The LLM dropped the semicolon the user asked for: the rule-based text is used.
    settings = CleanupSettings(llm_mode="always")
    pipeline = CleanupPipeline(settings, _Polisher("It's at four, bring your laptop."))
    pipeline.add_sentence("It's at four semicolon bring your laptop.")
    result = pipeline.finish(timeout=5)
    assert result.text == "It's at four; bring your laptop."
    assert any("spoken mark" in r for r in result.rejections)
    kept = CleanupPipeline(settings, _Polisher("It's at 4; bring your laptop."))
    kept.add_sentence("It's at four semicolon bring your laptop.")
    assert kept.finish(timeout=5).text == "It's at 4; bring your laptop."
