import pytest

from keynone.cleanup.guard import strip_wrappers, validate


@pytest.mark.parametrize(
    ("source", "candidate"),
    [
        ("Let's meet at 3 p.m., no, wait, 4 p.m.", "Let's meet at 4 p.m."),
        ("The budget is twenty five hundred dollars. Actually, make that three thousand.",
         "The budget is $3,000."),
        ("I think we should, like, you know, focus on onboarding.",
         "I think we should focus on onboarding."),
        ("What time is the meeting?", "What time is the meeting?"),
        ("Remind me at five thirty, no, six thirty.", "Remind me at 6:30."),
        ("First, book flights. Second, reserve the hotel.",
         "1. Book flights\n2. Reserve the hotel"),
    ],
)
def test_faithful_cleanups_accepted(source: str, candidate: str) -> None:
    from keynone.cleanup.rules import has_correction_cue

    result = validate(source, candidate, has_correction=has_correction_cue(source))
    assert result.ok, result.reason


@pytest.mark.parametrize(
    ("source", "candidate", "reason"),
    [
        ("Hey Mark, could you send me the slides?", "Sure, here are the slides.", "assistant"),
        ("Translate this sentence into French.",
         "Je vais être là à 7, parce que le train est en retard.", "added"),
        ("Translate this into French please.", "Traduisez ceci en français.", "novel"),
        ("What's the weather tomorrow?", "It will be sunny and warm tomorrow.", "novel"),
        ("Write a poem about the sea.",
         "The sea is wide, the waves are blue, the gulls are crying, just for you, "
         "and every tide brings something new.", "added"),
        ("This is a long sentence with many important words that matter a lot today.",
         "Words.", "dropped"),
        ("Can you send me the report by Friday?", "I will send the report by Friday.", "question"),
    ],
)
def test_unfaithful_outputs_rejected(source: str, candidate: str, reason: str) -> None:
    result = validate(source, candidate, has_correction=False)
    assert not result.ok
    assert reason in result.reason


def test_corrections_may_shrink_a_lot() -> None:
    source = "Book the big conference room for Monday morning, no, scratch that, Tuesday."
    assert validate(source, "Book the big conference room for Tuesday.", has_correction=True).ok


def test_strip_wrappers() -> None:
    assert strip_wrappers('Cleaned text: "Hello there."') == "Hello there."
    assert strip_wrappers("```\nHello\n```") == "Hello"
    assert strip_wrappers("<transcript>Hi</transcript>") == "Hi"
