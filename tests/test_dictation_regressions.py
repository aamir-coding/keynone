"""Regressions found by running real (synthesized) speech through the engine.

The recognizer splits sentences at pauses and sometimes omits punctuation;
these cases mirror its actual output on the test recordings.
"""

import pytest

from keynone.cleanup import rules
from keynone.cleanup.formatting import finalize, join_units
from keynone.cleanup.pipeline import CleanupPipeline, CleanupSettings


def dictate(*fragments: str) -> str:
    pipeline = CleanupPipeline(CleanupSettings(llm_mode="off"))
    for fragment in fragments:
        pipeline.add_sentence(fragment)
    text = pipeline.finish(timeout=2).text
    return finalize(text, style="default", drop_final_period=False, trailing_space=False)


def test_pause_inside_a_sentence_does_not_capitalize() -> None:
    assert dictate("Um so I was thinking ah", "that we we should push the launch to next week") == (
        "So I was thinking that we should push the launch to next week."
    )


def test_pause_after_non_final_word_continues_sentence() -> None:
    assert dictate("I talked to", "Sarah about the budget") == "I talked to Sarah about the budget."


def test_unpunctuated_sentences_get_periods() -> None:
    assert dictate("Thanks for the update", "The build is green now") == (
        "Thanks for the update. The build is green now."
    )


def test_unpunctuated_spoken_list() -> None:
    assert dictate("For the trip we need three things", "first, book the flights",
                   "second reserve the hotel", "and third rent a car") == (
        "For the trip we need three things:\n1. Book the flights\n2. Reserve the hotel\n"
        "3. Rent a car"
    )


def test_misheard_scratch_that_command() -> None:
    assert dictate("I'll call you tomorrow morning.", "Cratch that.", "I'll call you tonight") == (
        "I'll call you tonight."
    )


def test_scratch_that_followed_by_replacement_in_one_sentence() -> None:
    assert dictate("I'll call you tomorrow.", "Scratch that, I'll call you tonight.") == (
        "I'll call you tonight."
    )


def test_correction_without_commas() -> None:
    assert dictate("Let's meet at three PM no wait four PM at the cafe on Main Street") == (
        "Let's meet at 4 PM at the cafe on Main Street."
    )


def test_actually_make_that_inline() -> None:
    assert dictate("Let's plan the launch for Thursday, actually make that Friday so marketing "
                   "has time to prepare.") == (
        "Let's plan the launch for Friday so marketing has time to prepare."
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Call me at four thirty PM.", "Call me at 4:30 PM."),
        ("The call is at ten a.m. tomorrow.", "The call is at 10 a.m. tomorrow."),
        ("Let's meet at nine o'clock.", "Let's meet at 9 o'clock."),
        ("It starts at 7 pm sharp.", "It starts at 7 PM sharp."),
        ("We had one am I right moment.", "We had 1 AM I right moment."),
    ],
)
def test_time_formatting(raw: str, expected: str) -> None:
    assert rules.normalize_times(raw) == expected


def test_bare_ordinals_in_prose_are_not_a_list() -> None:
    text = "It was my first time and the second attempt went much better."
    assert rules.format_spoken_list(text) is None


def test_fuzzy_commands_do_not_fire_on_normal_sentences() -> None:
    assert rules.command_for("Catch that.") is None
    assert rules.command_for("New lines.") == "\n"
    assert rules.command_for("We need a new line of products.") is None


def test_join_units_adds_missing_periods_but_respects_lists_and_commas() -> None:
    assert join_units(["Thanks for the update", "See you soon"]) == (
        "Thanks for the update. See you soon"
    )
    assert join_units(["Agenda:\n1. Intro", "2. Budget"]) == "Agenda:\n1. Intro\n2. Budget"
    assert join_units(["Hi team,", "\n\n", "Update below."]) == "Hi team,\n\nUpdate below."


def test_delayed_period_still_lets_scratch_that_remove_the_sentence() -> None:
    # The recognizer flushed "...morning" at the pause; its period arrived later.
    assert dictate("I'll call you tomorrow morning", ".", "Cratch that.",
                   "I'll call you tonight") == "I'll call you tonight."


def test_delayed_comma_keeps_the_sentence_together() -> None:
    assert dictate("Hi team quick update on the mobile release",
                   ", the new onboarding flow is finished.") == (
        "Hi team quick update on the mobile release, the new onboarding flow is finished."
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Can you send me the quarterly report before Friday",
         "Can you send me the quarterly report before Friday?"),
        ("What time is the meeting", "What time is the meeting?"),
        ("Do it now", "Do it now."),
        ("Do you have a minute", "Do you have a minute?"),
        ("Have a nice weekend", "Have a nice weekend."),
        ("What a great demo", "What a great demo."),
        ("Is there any coffee left", "Is there any coffee left?"),
        ("The report is ready. Did you see it", "The report is ready. Did you see it?"),
    ],
)
def test_unpunctuated_questions_get_question_marks(raw: str, expected: str) -> None:
    assert finalize(raw, style="default", drop_final_period=False, trailing_space=False) == expected


def test_finalize_adds_final_period_except_for_code() -> None:
    assert finalize("ship it", style="default", drop_final_period=False,
                    trailing_space=False) == "ship it."
    assert finalize("git status", style="code", drop_final_period=False,
                    trailing_space=False) == "git status"
    assert finalize("sounds good", style="chat", drop_final_period=True,
                    trailing_space=False) == "sounds good"
