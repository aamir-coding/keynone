import pytest

from keynone.cleanup.formatting import adapt_to_context


def fit(text: str, before: str, after: str = "", style: str = "default",
        trailing_space: bool = True) -> str:
    return adapt_to_context(text, before, after, style=style, trailing_space=trailing_space)


@pytest.mark.parametrize(
    ("text", "before", "after", "expected"),
    [
        # empty field: like v1 (capitalized, trailing space for the next dictation)
        ("Hello world.", "", "", "Hello world. "),
        # mid-sentence after a space: lowercase common words, no extra space
        ("We should go.", "I think ", "", "we should go. "),
        # right after a word: add the separating space
        ("We should go.", "I think", "", " we should go. "),
        # new sentence after a period without space
        ("Next step.", "Done.", "", " Next step. "),
        ("Next step.", "Done. ", "", "Next step. "),
        # names, "I" and acronyms keep their case mid-sentence
        ("John.", "Call ", " today.", "John"),
        ("I agree.", "Well, ", "", "I agree. "),
        ("NASA called.", "and then ", "", "NASA called. "),
        ("iPhone sales grew.", "and ", "", "iPhone sales grew. "),
        # existing punctuation after the cursor: don't double it
        ("Everyone.", "Thanks", "!", " everyone"),
        ("And Bob.", "Alice, ", ".", "and Bob"),
        # the sentence continues after the cursor: no period, one space
        ("The new.", "Try ", "version first", "the new "),
        # a new sentence follows: keep the period and separate it
        ("We arrived.", "", "Then we left.", "We arrived. "),
        # opening bracket/quote before the cursor: no space; casing looks past it
        ("Maybe.", "Bring a jacket (", ")", "maybe"),
        ("Optional.", "Bring a jacket (", ")", "Optional"),  # unknown words keep case
        ("Next time.", "Done. (", ")", "Next time"),
        # lists start on their own line
        ("1. Intro\n2. Budget", "Agenda:", "", "\n1. Intro\n2. Budget"),
        ("1. Intro\n2. Budget", "Agenda:\n", "", "1. Intro\n2. Budget"),
        # line start capitalizes even after lowercase text on the previous line
        ("thanks.", "see you\n", "", "Thanks. "),
    ],
)
def test_adapt_to_context(text: str, before: str, after: str, expected: str) -> None:
    assert fit(text, before, after) == expected


def test_code_style_keeps_case() -> None:
    assert fit("Git status", "$ ", "", style="code", trailing_space=False) == "Git status"


def test_no_trailing_space_when_disabled() -> None:
    assert fit("Done.", "", "", trailing_space=False) == "Done."


def test_cjk_has_no_spaces() -> None:
    assert fit("明天见。", "好的，", "") == "明天见。"


def test_blank_text_unchanged() -> None:
    assert fit("   ", "abc", "def") == "   "
