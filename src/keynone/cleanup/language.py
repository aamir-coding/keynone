"""Lightweight language identification for transcript sentences.

The multilingual recognizer strips its language tag from the text, so cleanup
decides per sentence: a distinctive script identifies non-Latin languages
directly; Latin-script languages are scored by frequent function words.
Short or ambiguous text falls back to the given default.
"""

from __future__ import annotations

import re

# (character class, language) checked in order; kana before Han so Japanese wins.
_SCRIPTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"[぀-ヿ]"), "ja"),
    (re.compile(r"[가-힯ᄀ-ᇿ]"), "ko"),
    (re.compile(r"[一-鿿]"), "zh"),
    (re.compile(r"[ऀ-ॿ]"), "hi"),
    (re.compile(r"[؀-ۿ]"), "ar"),
    (re.compile(r"[֐-׿]"), "he"),
    (re.compile(r"[Ͱ-Ͽ]"), "el"),
    (re.compile(r"[฀-๿]"), "th"),
    (re.compile(r"[Ѐ-ӿ]"), "ru"),
)
_UKRAINIAN = re.compile(r"[іїєґІЇЄҐ]")

_STOPWORDS: dict[str, frozenset[str]] = {
    lang: frozenset(words.split())
    for lang, words in {
        "en": "the and to of a in is that it you for i we this on with be are have not was "
        "but they will what so can my your our me he she there do does did just about",
        "es": "el la de que y en los se del las un por con no una su para es al lo como más "
        "pero sus le ya o fue este ha sí porque esta muy también me hay mi yo",
        "fr": "le la de et les des en un une du est que qui dans pour pas sur au avec ce il "
        "elle je nous vous ne se plus mais ça on sont cette",
        "de": "der die und in den von zu das mit sich des auf für ist im dem nicht ein eine "
        "als auch es an werden aus er hat dass sie nach wird bei ich wir ihr",
        "it": "di e il la che è per un in non una sono mi lo ma ha le si con ci questo da io "
        "del della gli anche come",
        "pt": "de a o que e do da em um para é com não uma os no se na por mais as dos como "
        "mas foi ao ele das tem seu sua ou eu você",
        "nl": "de het een en van ik te dat die in is niet je zijn op aan met als voor er maar "
        "om ook we",
        "tr": "bir ve bu da de için ne ile çok ben o gibi daha var mı değil ama",
        "pl": "i w nie na się z że do to jest jak o co ale tak za od już",
        "vi": "và của là có không được cho một những người này với các trong đã",
        "sv": "och att det som en på är av för med till den har inte jag",
        "da": "og at det som en på er af for med til den har ikke jeg",
    }.items()
}
_WORD_RE = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)?")

LANGUAGE_NAMES: dict[str, str] = {
    "en": "English", "es": "Spanish", "fr": "French", "de": "German", "it": "Italian",
    "pt": "Portuguese", "nl": "Dutch", "tr": "Turkish", "pl": "Polish", "vi": "Vietnamese",
    "sv": "Swedish", "da": "Danish", "ru": "Russian", "uk": "Ukrainian", "hi": "Hindi",
    "ar": "Arabic", "he": "Hebrew", "el": "Greek", "th": "Thai", "ja": "Japanese",
    "ko": "Korean", "zh": "Chinese",
}


def base_language(code: str) -> str:
    """'en-US' -> 'en'; '' / 'auto' stay as they are."""
    return code.split("-")[0].lower()


def detect_language(text: str, default: str = "en") -> str:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return default
    for pattern, language in _SCRIPTS:
        hits = len(pattern.findall(text))
        if hits and hits >= 0.3 * len(letters):
            if language == "ru" and _UKRAINIAN.search(text):
                return "uk"
            return language
    words = [w.lower() for w in _WORD_RE.findall(text)]
    if len(words) < 3:
        return default
    scores = {lang: sum(w in stop for w in words) for lang, stop in _STOPWORDS.items()}
    best = max(scores, key=lambda lang: scores[lang])
    if scores[best] == 0 or scores[best] <= scores.get(default, 0):
        return default  # no evidence, or the default is tied for first place
    return best


def function_words(language: str) -> frozenset[str]:
    """Frequent function words of a language (empty set if unknown)."""
    return _STOPWORDS.get(base_language(language), frozenset())


def language_name(code: str) -> str:
    """Human-readable name for prompts ("es" -> "Spanish"); unknown codes pass through."""
    return LANGUAGE_NAMES.get(base_language(code), code)


_CODES_BY_NAME = {name.lower(): code for code, name in LANGUAGE_NAMES.items()}


def language_code(name_or_code: str) -> str:
    """"Spanish" / "spanish" / "es" / "es-ES" -> "es"; unknown names are lower-cased."""
    value = name_or_code.strip()
    return _CODES_BY_NAME.get(value.lower(), base_language(value))
