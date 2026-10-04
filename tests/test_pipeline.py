import time
from dataclasses import dataclass

from keynone.cleanup.pipeline import CleanupPipeline, CleanupSettings


@dataclass
class FakeResult:
    text: str
    truncated: bool = False


class FakePolisher:
    """Returns canned answers; records what it was asked."""

    def __init__(self, answers: dict[str, str] | None = None, delay: float = 0.0) -> None:
        self.answers = answers or {}
        self.delay = delay
        self.calls: list[str] = []

    def polish(self, transcript: str, *, deadline=None, on_text=None) -> FakeResult:
        self.calls.append(transcript)
        if self.delay:
            end = time.monotonic() + self.delay
            while time.monotonic() < end:
                if deadline is not None and time.monotonic() > deadline:
                    return FakeResult("", truncated=True)
                time.sleep(0.005)
        return FakeResult(self.answers.get(transcript, transcript))


def run(sentences, polisher=None, mode="auto", timeout=2.0) -> tuple[str, CleanupPipeline]:
    pipeline = CleanupPipeline(CleanupSettings(llm_mode=mode, llm_timeout=timeout), polisher)
    for sentence in sentences:
        pipeline.add_sentence(sentence)
    return pipeline.finish(timeout=timeout + 1).text, pipeline


def test_rules_only_dictation() -> None:
    text, _ = run(["Um, I think we we should ship it.", "It looks good."], mode="off")
    assert text == "I think we should ship it. It looks good."


def test_simple_correction_resolved_without_llm() -> None:
    polisher = FakePolisher()
    text, _ = run(["Let's meet on Tuesday.", "Actually, make that Wednesday."], polisher)
    assert text == "Let's meet on Wednesday."
    assert polisher.calls == []


def test_cross_sentence_correction_is_merged_for_the_llm() -> None:
    merged = "Invite Alex and Jordan. Actually, just Jordan."
    polisher = FakePolisher({merged: "Invite just Jordan."})
    text, _ = run(["Invite Alex and Jordan.", "Actually, just Jordan."], polisher)
    assert text == "Invite just Jordan."
    assert polisher.calls[-1] == merged


def test_llm_not_called_for_plain_sentences_in_auto_mode() -> None:
    polisher = FakePolisher()
    text, _ = run(["The report is attached.", "Thanks for your help."], polisher)
    assert text == "The report is attached. Thanks for your help."
    assert polisher.calls == []


def test_guard_rejection_falls_back_to_rules() -> None:
    source = "Can you, I mean, could you send the slides?"
    polisher = FakePolisher({source: "Sure, here are the slides you asked for."})
    text, pipeline = run([source], polisher)
    assert text == source
    assert pipeline._result.rejections


def test_llm_timeout_falls_back_to_rules() -> None:
    polisher = FakePolisher(delay=1.0)
    source = "Invite Alex and Jordan, actually just Jordan, to the review."
    text, pipeline = run([source], polisher, timeout=0.2)
    assert text == source
    assert "timeout" in pipeline._result.rejections


def test_scratch_that_removes_previous_sentence_across_units() -> None:
    text, _ = run(["I'll arrive at 5.", "Scratch that.", "I'll arrive at 6."], mode="off")
    assert text == "I'll arrive at 6."


def test_spoken_list_over_several_sentences() -> None:
    sentences = ["We need three things.", "First, book the flights.",
                 "Second, reserve the hotel.", "Third, rent a car."]
    text, _ = run(sentences, mode="off")
    assert text == (
        "We need three things:\n1. Book the flights\n2. Reserve the hotel\n3. Rent a car"
    )


def test_new_paragraph_between_sentences() -> None:
    text, _ = run(["Hi team,", "New paragraph.", "The build is green."], mode="off")
    assert text == "Hi team,\n\nThe build is green."


def test_pure_filler_dictation_is_empty() -> None:
    text, _ = run(["Um.", "Uh, hmm."], mode="off")
    assert text == ""


def test_preview_shows_progress() -> None:
    pipeline = CleanupPipeline(CleanupSettings(llm_mode="off"))
    pipeline.add_sentence("Hello, um, world.")
    deadline = time.monotonic() + 1
    while "world" not in pipeline.preview() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert pipeline.preview() == "Hello, world."
    pipeline.finish(1)
