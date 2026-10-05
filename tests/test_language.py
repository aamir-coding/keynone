import pytest

from keynone.cleanup.language import base_language, detect_language, language_name


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Let's meet at four PM at the cafe on Main Street.", "en"),
        ("Vamos a reunirnos mañana por la tarde en la oficina.", "es"),
        ("Wir treffen uns morgen um vier Uhr im Büro.", "de"),
        ("Nous allons nous retrouver demain dans le bureau.", "fr"),
        ("Ci vediamo domani pomeriggio, non è un problema per me.", "it"),
        ("Vamos nos encontrar amanhã para falar com você sobre o projeto.", "pt"),
        ("कल हम चार बजे ऑफिस में मिलेंगे।", "hi"),
        ("Завтра мы встретимся в четыре часа.", "ru"),
        ("Завтра ми зустрінемося о четвертій годині і поговоримо.", "uk"),
        ("明日の午後四時に会いましょう。", "ja"),
        ("明天下午四点我们见面。", "zh"),
        ("내일 오후 네 시에 만나요.", "ko"),
        ("سنلتقي غدا في المكتب.", "ar"),
    ],
)
def test_detect_language(text: str, expected: str) -> None:
    assert detect_language(text) == expected


@pytest.mark.parametrize("text", ["OK.", "Thanks!", "Sí.", "123 456", ""])
def test_short_or_ambiguous_text_uses_default(text: str) -> None:
    assert detect_language(text, default="en") == "en"


def test_default_wins_ties() -> None:
    assert detect_language("Hola amigo bueno", default="es") == "es"


def test_names() -> None:
    assert base_language("en-US") == "en"
    assert language_name("hi") == "Hindi"
    assert language_name("xx") == "xx"
