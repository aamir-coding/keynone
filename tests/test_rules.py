import pytest

from keynone.cleanup import rules
from keynone.cleanup.rules import SCRATCH, RuleOptions


def clean(text: str, **kw) -> str:
    return rules.clean_sentence(text, RuleOptions(**kw))


# --------------------------------------------------------------------------- fillers


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Um, so I was thinking, uh, we could meet on Tuesday.",
         "So I was thinking, we could meet on Tuesday."),
        ("I uh think so.", "I think so."),
        ("That's it, um.", "That's it."),
        ("Hmm. Let me check.", "Let me check."),
        ("Umm, uh, okay.", "Okay."),
        ("Uh, we need, um, more time.", "We need, more time."),
    ],
)
def test_fillers_removed(raw: str, expected: str) -> None:
    assert clean(raw) == expected


@pytest.mark.parametrize(
    "text",
    [
        "The drum is loud.",
        "Take him to the ER right now.",
        "Her error was small.",
        "Ahmed and Uhura arrived.",
        "Summer is here.",
    ],
)
def test_filler_lookalikes_kept(text: str) -> None:
    assert clean(text) == text


# --------------------------------------------------------------------------- repetitions


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("I I think so.", "I think so."),
        ("We, we should go.", "We should go."),
        ("I want to, I want to go home.", "I want to go home."),
        ("See you in the, in the morning.", "See you in the morning."),
        ("The the the end.", "The end."),
    ],
)
def test_repetitions_removed(raw: str, expected: str) -> None:
    assert clean(raw) == expected


@pytest.mark.parametrize(
    "text",
    [
        "I had had enough.",
        "It was very very good.",
        "I know that that is true.",
        "Call 555 555 1234.",
        "Knock knock.",
    ],
)
def test_intentional_repeats_kept(text: str) -> None:
    assert clean(text) == text


# --------------------------------------------------------------------------- dictionary


def test_dictionary_replacements_and_casing() -> None:
    options = dict(words=("PostgreSQL", "Keynone"), replacements=(("chat gpt", "ChatGPT"),))
    assert clean("I asked chat GPT about postgresql.", **options) == (
        "I asked ChatGPT about PostgreSQL."
    )
    assert clean("Chat-GPT and keynone.", **options) == "ChatGPT and Keynone."


# --------------------------------------------------------------------------- tidy


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("so I was thinking ,  we could", "So I was thinking, we could"),
        ("Hello , world .", "Hello, world."),
        ("Wait. . Next one.", "Wait. Next one."),
        (", hello there.", "Hello there."),
        ("I think i'm right and i know it.", "I think I'm right and I know it."),
        ("Use it, i.e. carefully.", "Use it, i.e. carefully."),
        ("iPhone sales grew.", "iPhone sales grew."),
        ("Is it ready ?.", "Is it ready?"),
    ],
)
def test_tidy(raw: str, expected: str) -> None:
    assert rules.tidy(raw) == expected


# --------------------------------------------------------------------------- commands


def test_standalone_new_line_command() -> None:
    assert clean("Thanks for the update. New line. Let me know.") == (
        "Thanks for the update.\nLet me know."
    )


def test_trailing_and_leading_paragraph_commands() -> None:
    assert clean("Hi team, new paragraph.") == "Hi team,\n\n"
    assert clean("New paragraph. The next topic is budget.") == (
        "\n\nThe next topic is budget."
    )


def test_mid_sentence_new_line_is_content() -> None:
    assert clean("Add a new line to the file.") == "Add a new line to the file."


def test_commands_can_be_disabled() -> None:
    assert clean("New line.", voice_commands=False) == "New line."


def test_scratch_that_within_text() -> None:
    text = clean("I'll be there at 5. Scratch that. I'll be there at 6.")
    resolved, dangling = rules.resolve_scratches(text)
    assert resolved == "I'll be there at 6."
    assert dangling == 0


def test_scratch_that_refers_to_earlier_text() -> None:
    resolved, dangling = rules.resolve_scratches(clean("Scratch that."))
    assert (resolved, dangling) == ("", 1)
    assert SCRATCH not in resolved


def test_drop_last_sentence() -> None:
    assert rules.drop_last_sentence("One. Two. Three.") == "One. Two."
    assert rules.drop_last_sentence("Only one.") == ""


# --------------------------------------------------------------------------- lists


def test_ordinal_list() -> None:
    text = ("For the trip we need three things. First, book the flights. Second, reserve the "
            "hotel, and third, rent a car.")
    assert rules.format_spoken_list(text) == (
        "For the trip we need three things:\n1. Book the flights\n2. Reserve the hotel\n"
        "3. Rent a car"
    )


def test_mid_clause_ordinals_and_outro() -> None:
    text = ("The steps are first, open the app and then log in, second, click export. "
            "Let me know if it works.")
    assert rules.format_spoken_list(text) == (
        "The steps are:\n1. Open the app and then log in\n2. Click export\n"
        "Let me know if it works."
    )


def test_number_markers_and_finally() -> None:
    text = "Number one, check the logs. Number two, restart the server. Finally, tell the team."
    assert rules.format_spoken_list(text) == (
        "1. Check the logs\n2. Restart the server\n3. Tell the team"
    )


def test_bullet_points() -> None:
    text = "Groceries: bullet point milk, bullet point eggs, bullet point bread."
    assert rules.format_spoken_list(text) == "Groceries:\n- Milk\n- Eggs\n- Bread"


@pytest.mark.parametrize(
    "text",
    [
        "First, let me say thanks for coming.",
        "At first I didn't like it, but now I do.",
        "Then we went home and next day we left.",
    ],
)
def test_not_a_list(text: str) -> None:
    assert rules.format_spoken_list(text) is None


# --------------------------------------------------------------------------- self-corrections


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Let's push the release to Thursday, no, actually Friday, so QA has more time.",
         "Let's push the release to Friday, so QA has more time."),
        ("Book a table for four, sorry, five people at 7.", "Book a table for five people at 7."),
        ("Send the invoice to accounting, I mean to finance, before noon.",
         "Send the invoice to finance, before noon."),
        ("The meeting is in room 204, no wait, room 205.", "The meeting is in room 205."),
        ("Remind me to call mom at five thirty, no, six thirty tonight.",
         "Remind me to call mom at six thirty tonight."),
        ("The total is fifteen hundred dollars. Sorry, fifteen hundred and fifty dollars.",
         "The total is fifteen hundred and fifty dollars."),
        ("We're flying out on the 12th. Actually, make it the 14th.",
         "We're flying out on the 14th."),
        ("Tell Priya the deadline is Monday, or rather Tuesday morning.",
         "Tell Priya the deadline is Tuesday morning."),
        ("Can you grab coffee, no, tea for me on your way in?",
         "Can you grab tea for me on your way in?"),
        ("The link expires in 24 hours, no, 48 hours.", "The link expires in 48 hours."),
        ("Set a timer for 20 minutes, no, 30.", "Set a timer for 30 minutes."),
        ("I'll send it tomorrow. Actually, scratch that, I'll send it tonight.",
         "I'll send it tonight."),
        ("Let's meet at 3 p.m., no, wait, 4 p.m. at the cafe.", "Let's meet at 4 p.m. at the cafe."),
        ("I'll be there by 6. Sorry, I meant 7, because the train is late.",
         "I'll be there by 7, because the train is late."),
        ("Second, update the deck, no wait, update the budget sheet.",
         "Second, update the budget sheet."),
    ],
)
def test_corrections_resolved(raw: str, expected: str) -> None:
    assert rules.resolve_corrections(raw) == expected


@pytest.mark.parametrize(
    "text",
    [
        "We're late, sorry, traffic was bad.",
        "Invite Alex and Jordan, actually just Jordan, to the review.",
        "Can you, I mean, could you send the slides?",
        "I said no, and I mean it.",
        "Did you finish it? No, I didn't.",
        "It's, I mean, pretty good.",
        "Sorry for the late reply.",
    ],
)
def test_ambiguous_corrections_left_alone(text: str) -> None:
    assert rules.resolve_corrections(text) == text


# --------------------------------------------------------------------------- cues


@pytest.mark.parametrize(
    "text",
    [
        "Let's meet at 3, no wait, 4.",
        "Send it to John, I mean Sarah.",
        "Actually, make that Friday.",
        "It costs 20, sorry, 30 dollars.",
        "We'll take the train, or rather the bus.",
        "I'll call at 5, no, 6.",
    ],
)
def test_correction_cues(text: str) -> None:
    assert rules.has_correction_cue(text)


@pytest.mark.parametrize("text", ["Tell me about it.", "The note is on the desk.", "Know-how."])
def test_no_correction_cue(text: str) -> None:
    assert not rules.has_correction_cue(text)


def test_starts_with_correction_cue() -> None:
    assert rules.starts_with_correction_cue("Actually, make that Friday.")
    assert rules.starts_with_correction_cue("No, wait, Thursday.")
    assert not rules.starts_with_correction_cue("We should actually go.")


def test_soft_fillers() -> None:
    assert rules.has_soft_fillers("I think, like, we should go.")
    assert rules.has_soft_fillers("It's, you know, complicated.")
    assert not rules.has_soft_fillers("I like pizza.")


def test_list_cues() -> None:
    assert rules.has_list_cue("First, do this. Second, do that.")
    assert not rules.has_list_cue("First, do this.")
    assert rules.starts_list_item("Second, reserve the hotel.")
