"""Language-aware cleanup: English-only rules never touch other languages."""

import pytest

from keynone.cleanup import rules
from keynone.cleanup.formatting import finalize, join_units
from keynone.cleanup.guard import validate
from keynone.cleanup.pipeline import CleanupPipeline, CleanupSettings
from keynone.cleanup.rules import RuleOptions


def clean(text: str, language: str) -> str:
    return rules.clean_sentence(text, RuleOptions(language=language))


@pytest.mark.parametrize(
    ("text", "language"),
    [
        ("Wir treffen uns um vier Uhr.", "de"),  # "um" = "at", not a filler
        ("Er kommt morgen und er bleibt.", "de"),  # "er" = "he"
        ("Ele comprou um carro novo.", "pt"),  # "um" = "a"
        ("Die Frau, die die Zeitung liest.", "de"),  # grammatical "die die"
        ("Ho visto i ragazzi ieri.", "it"),  # "i" is an article, not "I"
    ],
)
def test_words_that_are_fillers_in_english_survive(text: str, language: str) -> None:
    assert clean(text, language) == text


@pytest.mark.parametrize(
    ("text", "language", "expected"),
    [
        ("Ähm, ich schicke dir den Bericht morgen.", "de", "Ich schicke dir den Bericht morgen."),
        ("Euh, on peut se voir demain.", "fr", "On peut se voir demain."),
        ("Eh, creo que, eh, está bien.", "es", "Creo que está bien."),
        ("Mmm, ok.", "it", "Ok."),
    ],
)
def test_language_specific_fillers(text: str, language: str, expected: str) -> None:
    assert clean(text, language) == expected


def test_english_rules_unchanged() -> None:
    assert clean("Um, I I think we should go at five thirty pm.", "en") == (
        "I think we should go at 5:30 PM."
    )


def test_multilingual_voice_commands() -> None:
    assert rules.command_for("Nueva línea.") == "\n"
    assert rules.command_for("Neuer Absatz.") == "\n\n"
    assert rules.command_for("Efface ça.") == rules.SCRATCH


@pytest.mark.parametrize(
    ("text", "language", "cue"),
    [
        ("Te llamo el lunes, no, mejor el martes.", "es", True),
        ("Ich komme morgen, ich meine übermorgen.", "de", True),
        ("On se voit demain, je veux dire jeudi.", "fr", True),
        ("Te llamo el lunes por la mañana.", "es", False),
        ("Wir sehen uns morgen im Büro.", "de", False),
    ],
)
def test_correction_cues_in_other_languages(text: str, language: str, cue: bool) -> None:
    assert rules.has_correction_cue(text, language) is cue


def test_strict_guard_rejects_invented_words_and_new_questions() -> None:
    source = "No preguntes qué puede hacer tu país por ti. Pregunta qué puedes hacer tú."
    damaged = "No preguntes qué tu país te ajuda. ¿Qué puedes hacer tú?"
    assert not validate(source, damaged, has_correction=False, strict=True).ok
    assert validate(source, source, has_correction=False, strict=True).ok


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def polish(self, transcript, *, variant="en", target="English", deadline=None,
               on_text=None):
        self.calls.append((transcript, variant))

        class R:
            text = transcript
            truncated = False

        return R()


def test_plain_non_english_sentences_skip_the_llm() -> None:
    recorder = _Recorder()
    pipeline = CleanupPipeline(CleanupSettings(language="auto"), recorder)
    pipeline.add_sentence("No preguntes qué puede hacer tu país por ti.")
    text = pipeline.finish(timeout=2).text
    assert text == "No preguntes qué puede hacer tu país por ti."
    assert recorder.calls == []


def test_non_english_corrections_use_the_multilingual_prompt() -> None:
    recorder = _Recorder()
    pipeline = CleanupPipeline(CleanupSettings(language="auto"), recorder)
    pipeline.add_sentence("Te llamo el lunes, no, mejor el martes por la mañana.")
    pipeline.finish(timeout=2)
    assert recorder.calls and recorder.calls[0][1] == "multi"


def test_translation_uses_translate_prompt_and_skips_target_language() -> None:
    recorder = _Recorder()
    pipeline = CleanupPipeline(CleanupSettings(language="auto", translate_to="English"),
                               recorder)
    pipeline.add_sentence("Wir treffen uns morgen um vier Uhr im Büro.")
    pipeline.add_sentence("See you there, thanks a lot.")
    pipeline.finish(timeout=2)
    variants = [variant for _, variant in recorder.calls]
    assert variants == ["translate"]  # the English sentence is not "translated"


def test_language_aware_sentence_endings() -> None:
    assert finalize("明天下午四点见", style="default", drop_final_period=False,
                    trailing_space=False) == "明天下午四点见。"
    assert finalize("कल मिलते हैं", style="default", drop_final_period=False,
                    trailing_space=False) == "कल मिलते हैं।"
    assert finalize("Wir sehen uns morgen", style="default", drop_final_period=False,
                    trailing_space=False) == "Wir sehen uns morgen."
    assert join_units(["明天见。", "好的。"]) == "明天见。好的。"
