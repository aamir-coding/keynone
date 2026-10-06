"""Deterministic, instant cleanup rules.

Applied to every unit before (and as the fallback for) LLM polishing:

* filler words (um, uh, er, hmm ...; "you know" / "like" between commas) and
  stutters / immediately repeated phrases
* spoken commands: "new line", "new paragraph" (also mid-sentence), "scratch that"
* personal-dictionary spellings
* self-corrections: "at three, no wait, four", "Monday no Tuesday",
  "... by six. Sorry, I meant seven", "... scratch that, six thirty"
* spoken lists ("first ... second ... finally", "number one ...", "one ... two ...
  three ...", "we need three things: a, b and c", "bullet point ...")
* punctuation / capitalization tidy-up after the edits above

Everything here is conservative: a correction is applied only when the replaced
words can be identified confidently (same kind of value, a restarted phrase, a
swapped name ...); ambiguous cases are left for the LLM, which can tell filler
from meaning, or left as dictated.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass

from keynone.cleanup.language import function_words

# Marker inserted for a spoken "scratch that"; resolved by the pipeline.
SCRATCH = "␡"  # "SYMBOL FOR DELETE": never produced by ASR

# --------------------------------------------------------------------------- fillers

# Non-lexical fillers per language. English "um"/"er" are real words elsewhere
# (German "um" = "at", "er" = "he"; Portuguese "um" = "a"), so each language
# only gets sounds that are never words in it.
_FILLERS_BY_LANGUAGE: dict[str, str] = {
    "en": r"u+h*m+|u+h+|e+r+m+|e+r|a+h+|h+m+|m+h*m+|uh-huh",
    "de": r"ä+h+m*|ö+h+m*|h+m+|m+h*m+",
    "fr": r"e+u+h+|h+e+u+|h+u+m+|h+m+|m+h*m+",
    "es": r"e+h+|e+m+|e+h+m+|h+m+|m+h*m+",
    "it": r"e+h+m*|h+m+|m+h*m+",
    "pt": r"h+ã+|a+h+n+|h+m+|m+h*m+",
    "nl": r"e+h+m*|u+h+m*|h+m+|m+h*m+",
}
_GENERIC_FILLERS = r"h+m+|m+h*m+"
# English soft fillers that are removed by rule when the recognizer set them off
# with commas ("it's, you know, a small change"); elsewhere they go to the LLM.
_SOFT_FILLERS = r"you\s+know|like|basically|literally"
# A filler between commas takes both commas along ("I was, uh, going"), unless
# the first comma closes an introductory clause ("When I got there, um, ...").
_KEEP_COMMA_AFTER = frozenset(
    """when if after before because since although though while once unless until as
    whenever wherever well okay ok yes yeah no hi hello hey dear thanks oh alright right sure
    anyway anyways""".split()
)
_LEADING_SOFT_RE = re.compile(
    r"(?i)(^|[.!?…]\s+)(?:you\s+know|like|basically|literally),\s+(?=[^\W_])")
_TRAILING_SOFT_RE = re.compile(r"(?i),\s*(?:you\s+know|basically|literally)(?=\s*[.!?…]|\s*$)")
_FILLER_RES: dict[str, tuple[re.Pattern[str], re.Pattern[str]]] = {}


def _filler_patterns(language: str) -> tuple[re.Pattern[str], re.Pattern[str]]:
    if language not in _FILLER_RES:
        fillers = _FILLERS_BY_LANGUAGE.get(language, _GENERIC_FILLERS)
        wrapped = f"{fillers}|{_SOFT_FILLERS}" if language == "en" else fillers
        _FILLER_RES[language] = (
            # ", um, going" and ", um going" (the pause before the filler made the comma)
            re.compile(rf"(?i),\s*(?:(?:{wrapped})(?:\s*,\s*|\s+))+(?=[^\W_])"),
            re.compile(
                rf"(?i)(^|[\s,;:.!?…\"'(¿¡])(?:{fillers})(?:,|…|\.\.\.)?(?=$|[\s,;:.!?…\"')])"
            ),
        )
    return _FILLER_RES[language]


def _filler_sub(m: re.Match[str]) -> str:
    word = m.group(0)[len(m.group(1)) :].rstrip(",.…")
    if len(word) > 1 and word.isupper():  # "ER", "AH": acronyms, not fillers
        return m.group(0)
    return m.group(1)


def _wrapped_filler_sub(m: re.Match[str]) -> str:
    before = m.string[: m.start()]
    clause = re.split(r"[.!?…;]\s+", before)[-1].split()
    first = clause[0].lower().strip(",") if clause else ""
    return ", " if first in _KEEP_COMMA_AFTER else " "


def remove_fillers(text: str, language: str = "en") -> str:
    """Remove non-lexical fillers (um, uh, er, hmm, äh, euh ...).

    In English, "you know" / "like" / "basically" / "literally" set off by
    commas are fillers too ("It's, you know, a small change").
    """
    wrapped, single = _filler_patterns(language)
    text = wrapped.sub(_wrapped_filler_sub, text)
    if language == "en":
        text = _LEADING_SOFT_RE.sub(r"\1", text)
        text = _TRAILING_SOFT_RE.sub("", text)
    previous = None
    while previous != text:  # adjacent fillers ("um, uh,") need repeated passes
        previous = text
        text = single.sub(_filler_sub, text)
    return text


# --------------------------------------------------------------------------- repetitions

_NUMBER_WORD_SET = frozenset(
    """zero oh one two three four five six seven eight nine ten eleven twelve thirteen
    fourteen fifteen sixteen seventeen eighteen nineteen twenty thirty forty fifty sixty
    seventy eighty ninety hundred thousand million double triple""".split()
)
# Doubled words that are usually intentional (emphasis, interjections, digits).
_KEEP_DOUBLE = frozenset(
    """that had is very really so no bye ha haha knock yeah yes go now well there tut chop bla
    blah la hey hi sí si muy ja nein oui non très sehr molto não nee much many more long far
    big little too way super please again over round ever never""".split()
) | _NUMBER_WORD_SET
# Repeated phrases that are emphasis, not restarts.
_KEEP_PHRASES = frozenset(
    {"come on", "go on", "hurry up", "thank you", "well done", "oh my", "more and more"}
)
_WORD = r"[^\W\d_](?:[\w'’]*[^\W_])?"  # a word that starts with a letter
_REPEAT_WORD_RE = re.compile(rf"(?i)\b({_WORD})(?:-?[\s,]+\1\b)+")
_REPEAT_NGRAM_RE = re.compile(rf"(?i)\b((?:{_WORD}[\s,]+){{1,5}}{_WORD})[\s,]+\1\b")


def remove_repetitions(text: str, language: str = "en") -> str:
    """Remove stutters ("I I think") and restarted phrases ("I want to, I want to go").

    Outside English, doubled function words are kept: they are often
    grammatical (German "die die", "das das" in relative clauses).
    """
    keep = _KEEP_DOUBLE if language == "en" else _KEEP_DOUBLE | function_words(language)

    def word_sub(m: re.Match[str]) -> str:
        word = m.group(1).lower()
        if word == "is" and language == "en":  # "what it is is" is grammatical
            sentence = re.split(r"[.!?…]\s+", m.string[: m.start()])[-1].lower()
            return m.group(0) if re.search(r"\bwhat\b", sentence) else m.group(1)
        return m.group(0) if word in keep else m.group(1)

    def ngram_sub(m: re.Match[str]) -> str:
        words = m.group(1).lower().replace(",", " ").split()
        if " ".join(words) in _KEEP_PHRASES or all(w in _NUMBER_WORD_SET for w in words):
            return m.group(0)
        return m.group(1)

    previous = None
    for _ in range(4):
        if previous == text:
            break
        previous = text
        text = _REPEAT_NGRAM_RE.sub(ngram_sub, text)
        text = _REPEAT_WORD_RE.sub(word_sub, text)
    return text


# --------------------------------------------------------------------------- dictionary


def _phrase_pattern(phrase: str) -> re.Pattern[str] | None:
    words = phrase.split()
    if not words:
        return None
    body = r"[\s-]*".join(re.escape(w) for w in words)
    return re.compile(rf"(?i)(?<![\w]){body}(?![\w])")


def _literal(value: str) -> Callable[[re.Match[str]], str]:
    """Replacement function: inserts ``value`` verbatim (no backslash escapes)."""
    return lambda _match: value


def apply_dictionary(
    text: str, words: Iterable[str] = (), replacements: Mapping[str, str] | None = None
) -> str:
    """Apply user replacements, then enforce the casing of dictionary words."""
    for spoken, written in (replacements or {}).items():
        pattern = _phrase_pattern(spoken)
        if pattern:
            text = pattern.sub(_literal(written), text)
    for word in words:
        pattern = _phrase_pattern(word)
        if pattern:
            text = pattern.sub(_literal(word), text)
    return text


# --------------------------------------------------------------------------- tidy-up

_ABBREVIATIONS = frozenset(
    "mr mrs ms dr st jr sr vs etc e.g i.e approx no fig dept inc ltd co prof a.m p.m".split()
)


def ends_with_abbreviation(text: str) -> bool:
    """True if ``text`` ends with e.g. "Dr." / "p.m." rather than a sentence end."""
    tokens = text.split()
    return bool(tokens) and tokens[-1].lower().rstrip(".") in _ABBREVIATIONS


def _collapse_punct(m: re.Match[str]) -> str:
    marks = re.sub(r"\s+", "", m.group(0))
    for mark in "?!.":
        if mark in marks:
            return mark
    return marks[0]


def _capitalize(text: str) -> str:
    def cap(m: re.Match[str]) -> str:
        prefix, word = m.group(1), m.group(2)
        before = text[: m.start(2)].rstrip()
        last_token = before.split()[-1].lower().rstrip(".") if before.split() else ""
        if before.endswith(".") and last_token in _ABBREVIATIONS:
            return m.group(0)
        if any(c.isupper() for c in word[1:]):  # iPhone, eBay, macOS
            return m.group(0)
        return prefix + word[0].upper() + word[1:]

    return re.sub(r"(^|[.!?…]\s+|\n)([^\W\d_][\w'’-]*)", cap, text)


def upper_first(text: str) -> str:
    """Capitalize the first letter (after leading punctuation/space)."""
    for index, char in enumerate(text):
        if char.isalpha():
            return text[:index] + char.upper() + text[index + 1 :]
        if char.isdigit():
            break
    return text


def tidy(text: str, language: str = "en") -> str:
    """Fix spacing/punctuation/capitalization left behind by edits."""
    text = text.replace("...", "…")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r" +([,;:.!?…])", r"\1", text)
    text = re.sub(r"[,;:.!?](?:\s*[,;:.!?])+", _collapse_punct, text)
    text = re.sub(r"(^|\n)[\s,;:.]+", r"\1", text)
    text = re.sub(r"([,;])(?=[^\W\d_])", r"\1 ", text)
    if language == "en":  # "i" -> "I" (in Italian "i" is an article)
        text = re.sub(r"(?<![\w.'’-])i(?=(?:['’](?:m|d|ll|ve|s))?(?![\w.'’-]))", "I", text)
    text = _capitalize(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip(" ")


# --------------------------------------------------------------------------- sentences

# Line breaks first: "first paragraph.\n\nThe second" keeps its break.
_SENTENCE_SPLIT_RE = re.compile(r"([ \t]*\n\s*)|(?<=[.!?…])[ \t]+(?=\S)")
_SENTENCE_END_RE = re.compile(r"[.!?…]+[\"')\]]*\s+(?=\S)|\n+")


def split_sentences(text: str) -> list[str]:
    """Split into sentences; runs of newlines are kept as their own items."""
    parts: list[str] = []
    for piece in _SENTENCE_SPLIT_RE.split(text):
        if not piece:
            continue
        if not piece.strip():
            if "\n" in piece:
                parts.append("\n" * min(piece.count("\n"), 2))
            continue
        if parts and not parts[-1].startswith("\n"):
            tokens = parts[-1].split()
            last = tokens[-1].lower().rstrip(".") if tokens else ""
            if last in _ABBREVIATIONS:  # "Dr. Smith" is one sentence
                parts[-1] = f"{parts[-1]} {piece}"
                continue
        parts.append(piece.strip())
    return [p for p in parts if p]


def join_sentences(parts: Iterable[str]) -> str:
    out = ""
    for part in parts:
        if not out or out.endswith("\n") or part.startswith("\n"):
            out += part
        else:
            out += " " + part
    return out


def last_sentence_start(text: str) -> int:
    """Index in ``text`` where its last sentence (or line) starts."""
    start = 0
    for m in _SENTENCE_END_RE.finditer(text):
        if not m.group(0).startswith("\n") and ends_with_abbreviation(text[: m.end()].strip()):
            continue
        start = m.end()
    return start


# --------------------------------------------------------------------------- questions

_PERSONS = r"(?:you|we|i|they|he|she|anyone|someone|somebody|anybody|everyone|everybody)"
_SUBJECTS = (rf"(?:{_PERSONS[3:-1]}|it|everything|anything|something|nothing|there|this|that"
             r"|these|those)")
_QUESTION_START_RE = re.compile(
    r"(?i)^(?:"
    r"(?:what(?!\s+an?\b)|why|how|where|when|who|whom|whose|which)(?:['’]s)?\b"
    # "do"/"have" are also imperatives ("Do it now", "Have a look"): people only
    rf"|(?:do|have)\s+{_PERSONS}\b"
    r"|(?:can|could|would|will|shall|should|may|might|must|does|did|is|are|am|was|were|has|had"
    r"|isn['’]t|aren['’]t|wasn['’]t|weren['’]t|don['’]t|doesn['’]t|didn['’]t|won['’]t"
    r"|wouldn['’]t|can['’]t|couldn['’]t|shouldn['’]t|haven['’]t|hasn['’]t)"
    rf"\s+(?:{_SUBJECTS}|the|my|your|our|their|his|her|its|a|an|any|all|each|every)\b)"
)
# Tag questions: "..., shall we", "..., right", "isn't it" at the end.
_TAG_QUESTION_RE = re.compile(
    r"(?i)(?:,\s*(?:right|okay|ok|correct|yeah)"
    r"|(?:,\s*|\s)(?:shall\s+we|isn['’]t\s+it|aren['’]t\s+you|don['’]t\s+you(?:\s+think)?"
    r"|won['’]t\s+you|can['’]t\s+we|didn['’]t\s+you|wouldn['’]t\s+it|isn['’]t\s+that\s+right"
    r"|does\s+that\s+work|is\s+that\s+okay|is\s+that\s+ok))\s*$"
)


# "What it is is a bug", "What I need is time": statements.
_PSEUDO_CLEFT_RE = re.compile(
    r"(?i)^what\s+(?:i|we|you|they|he|she|it|this|that|everyone)\s+(?:\S+\s+){0,2}?"
    r"(?:is|was|are|were)\b")
_INTERJECTION_RE = re.compile(r"(?i)^(?:(?:hey|hi|so|okay|ok|oh|well|um|uh|and|but|also"
                              r"|alright|right)[,.!]?\s+)+")


def is_question(sentence: str) -> bool:
    """English question heuristic for unpunctuated sentences."""
    sentence = _INTERJECTION_RE.sub("", sentence.strip().rstrip(".!?…"))
    if _PSEUDO_CLEFT_RE.match(sentence):
        return False
    return bool(_QUESTION_START_RE.match(sentence) or _TAG_QUESTION_RE.search(sentence))


# --------------------------------------------------------------------------- commands

_NEWLINE_CMDS = {
    "new line", "newline", "next line",
    "nueva línea", "nouvelle ligne", "neue zeile", "nuova riga", "nova linha", "nieuwe regel",
}
_PARAGRAPH_CMDS = {
    "new paragraph", "next paragraph",
    "nuevo párrafo", "nouveau paragraphe", "neuer absatz", "nuovo paragrafo", "novo parágrafo",
    "nieuwe alinea",
}
_SCRATCH_CMDS = {
    "scratch that", "delete that", "strike that", "erase that", "scratch that please",
    "scratch this", "scratch it", "scratched that", "strike this", "delete this", "undo that",
    "delete the last sentence", "remove the last sentence", "delete last sentence",
    "borra eso", "efface ça", "lösch das", "cancella questo", "apaga isso", "verwijder dat",
}
# Misrecognitions that only count as "scratch that" when spoken on their own.
_SCRATCH_EXACT_ONLY = {"cross that", "cross that out", "scratch dat"}
# Spoken before a command ("No, scratch that", "Oh scratch that").
_COMMAND_LEAD_RE = re.compile(
    r"^(?:(?:no|nope|oh|oops|um|uh|er|ah|hmm|okay|ok|so|well|actually|wait|sorry"
    r"|you know what)\s+)+"
)
_GREETING_RE = re.compile(
    r"(?i)^(?:hi|hello|hey|dear|good (?:morning|afternoon|evening)|greetings)\b")


def _edit_distance(a: str, b: str) -> int:
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            current.append(min(previous[j] + 1, current[j - 1] + 1,
                               previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def _words_match(spoken: str, phrase: str) -> bool:
    """Same words, allowing one recognition slip in words of 4+ letters."""
    a, b = spoken.split(), phrase.split()
    if len(a) != len(b):
        return False
    return all(x == y or (len(y) >= 4 and _edit_distance(x, y) <= 1)
               for x, y in zip(a, b, strict=True))


_COMMANDS: tuple[tuple[frozenset[str] | set[str], str], ...] = (
    (_NEWLINE_CMDS, "\n"), (_PARAGRAPH_CMDS, "\n\n"), (_SCRATCH_CMDS, SCRATCH),
)


def command_for(sentence: str) -> str | None:
    """The command a standalone spoken sentence stands for, if any.

    Tolerates one small recognition slip per word ("cratch that", "new lines")
    and a lead-in ("No, scratch that"), while ordinary sentences ("catch that")
    do not match.
    """
    key = re.sub(r"[^\w\s]", " ", sentence).strip().lower()
    key = re.sub(r"\s+", " ", key)
    if not key or len(key.split()) > 6:
        return None
    for phrases, command in _COMMANDS:
        if key in phrases:
            return command
    if key in _SCRATCH_EXACT_ONLY:
        return SCRATCH
    stripped = _COMMAND_LEAD_RE.sub("", key)
    if stripped != key and (stripped in _SCRATCH_CMDS or stripped in _SCRATCH_EXACT_ONLY
                            or any(_words_match(stripped, p) for p in _SCRATCH_CMDS)):
        return SCRATCH
    for phrases, command in _COMMANDS:
        if any(_words_match(key, phrase) for phrase in phrases):
            return command
    return None


# "new paragraph" / "new line" inside a sentence ("... opening paragraph, new
# paragraph this is the closing ..."), unless used as an ordinary noun
# phrase ("a new line of products", "read the next paragraph").
_BREAK_RE = re.compile(
    r"(?i)(?<![\w'’-])(?:(?P<para>(?:new|next)[\s-]?(?:paragraph|para)s?)"
    r"|(?P<line>new[\s-]?lines?|next[\s-]line))(?P<post>[.,;:!?…]*)(?![\w'’-])"
)
_LITERAL_BEFORE = frozenset(
    """a an the this that these those our your my his her its their each every another any
    some one first second last whole entire single blank empty extra separate same following
    previous brand completely totally entirely""".split()
)
_LITERAL_AFTER = frozenset(
    """of manager managers management item items character characters break breaks feed feeds
    number numbers chart charts symbol symbols separator separators spacing height width tool
    tools button buttons key keys""".split()
)


def _is_literal_break(text: str, m: re.Match[str]) -> bool:
    before = re.findall(r"[\w'’]+", text[: m.start()].lower())
    after = re.findall(r"[\w'’]+", text[m.end() :].lower())
    if (before and before[-1] in _LITERAL_BEFORE) or (after and after[0] in _LITERAL_AFTER):
        return True
    # "see next paragraph", "continued on next line"
    return m.group(0).lower().startswith("next") and bool(before) and before[-1] in (
        "see", "read", "in", "on", "to", "at", "into", "from", "of", "check", "skip", "go")


def strip_trailing_break(text: str) -> str | None:
    """``text`` without a spoken "new paragraph"/"new line" at its very end, or None."""
    matches = [m for m in _BREAK_RE.finditer(text) if not _is_literal_break(text, m)]
    if matches and not text[matches[-1].end() :].strip(" ,.;:"):
        return text[: matches[-1].start()].rstrip(" ,;:")
    return None


def _last_sentence(line: str) -> str:
    parts = [p for p in re.split(r"(?<=[.!?…])\s+", line.strip()) if p]
    return parts[-1] if parts else line


def close_before_break(text: str) -> str:
    """End the text that precedes a spoken line/paragraph break.

    Sentences get "." (or "?"), short greetings a comma ("Hi Sarah,"), and
    one- or two-word lines nothing at all: labels, list-like lines and
    sign-offs ("Milk", "Best regards").
    """
    body = text.rstrip(" ,;:")
    if not body.strip() or body.endswith("\n") or body[-1] in ".!?…":
        return body
    sentence = _last_sentence(body.rsplit("\n", 1)[-1].strip())
    words = len(sentence.split())
    if words <= 4 and _GREETING_RE.match(sentence):
        return body + ","
    if words <= 2:  # keep a spoken comma: "Thanks," [new line] "Ravi"
        return body + ("," if text.rstrip().endswith(",") else "")
    return body + ("?" if is_question(sentence) else ".")


def close_sentence(text: str) -> str:
    """End an unpunctuated sentence with "." (or "?" for a question)."""
    body = text.rstrip(" ,;:")
    if not body or body[-1] in ".!?…":
        return body
    return body + ("?" if is_question(_last_sentence(body)) else ".")


def _split_break_commands(sentence: str) -> list[str]:
    parts: list[str] = []
    position = 0
    for m in _BREAK_RE.finditer(sentence):
        if _is_literal_break(sentence, m):
            continue
        before = sentence[position : m.start()].strip()
        if before:
            parts.append(close_before_break(before))
        parts.append("\n\n" if m.group("para") else "\n")
        position = m.end()
    rest = sentence[position:].strip().lstrip(",;:. ")
    if rest:
        parts.append(upper_first(rest) if position else rest)
    # every line after a break starts with a capital ("Milk\nEggs\nBread")
    return [upper_first(p) if i and parts[i - 1].startswith("\n") else p
            for i, p in enumerate(parts)]


def apply_voice_commands(text: str, language: str = "en") -> str:
    """Turn spoken commands into line breaks / scratch markers."""
    out: list[str] = []
    for sentence in split_sentences(text):
        if sentence.startswith("\n"):
            out.append(sentence)
            continue
        command = command_for(sentence)
        if command is not None:
            out.append(command)
        elif language == "en":
            out.extend(_split_break_commands(sentence))
        else:
            out.append(sentence)
    return join_sentences(out)


def resolve_scratches(text: str) -> tuple[str, int]:
    """Apply scratch markers inside ``text``.

    Returns the resolved text and how many scratches were left unresolved
    because they refer to sentences *before* this text (earlier units).
    """
    kept: list[str] = []
    dangling = 0
    for part in split_sentences(text.replace(SCRATCH, f" {SCRATCH}. ")):
        if part.strip(" .") == SCRATCH:
            # Drop trailing breaks, then the previous sentence.
            while kept and kept[-1].startswith("\n"):
                kept.pop()
            if kept:
                kept.pop()
            else:
                dangling += 1
            continue
        kept.append(part)
    return join_sentences(kept), dangling


def drop_last_sentence(text: str) -> str:
    parts = split_sentences(text)
    while parts and parts[-1].startswith("\n"):
        parts.pop()
    if parts:
        parts.pop()
    return join_sentences(parts)


# --------------------------------------------------------------------------- word tokens


@dataclass(frozen=True)
class _Tok:
    raw: str
    norm: str
    start: int
    end: int


def _norm(word: str) -> str:
    return word.lower().replace("’", "'").strip(".,;:!?\"()“”‘")


def _tokens(text: str) -> list[_Tok]:
    tokens = []
    for m in re.finditer(r"\S+", text):
        norm = _norm(m.group())
        if norm:
            tokens.append(_Tok(m.group(), norm, m.start(), m.end()))
    return tokens


_FUNCTION_WORDS = frozenset(
    """a an the this that these those my your his her its our their i you he she we they it me
    him us them is are was were be been am do does did have has had will would can could
    should may might must to of in on at for by with from and or but so if as not no yes
    just also very really there here it's i'm we're they're you're i'll we'll i've let's what""".split()
)
_DETERMINERS = frozenset(
    "a an the this that these those my your his her its our their each every any some no".split()
)
_PREPOSITIONS = frozenset(
    """at in on to for by with from about into onto over under after before until near
    around via""".split()
)
_CONJUNCTIONS = frozenset("and or but then so".split())
_SUBJECT_PRONOUNS = frozenset(
    """i i'm i'll i've i'd we we're we'll we've we'd you you're you'll you've he he's she she's
    they they're they'll it it's it'll there there's let's""".split()
)
_AUXILIARIES = frozenset(
    """is are was were be been am will would can could should shall may might must has have
    had do does did 's 're won't can't isn't aren't wasn't weren't don't doesn't didn't
    haven't hasn't wouldn't couldn't shouldn't""".split()
)
_JOINERS = frozenset(
    "for to at in on by with from and or please instead tomorrow today tonight".split()
)
# Common words: capitalized, these start sentences rather than name things.
_COMMON_WORDS = _FUNCTION_WORDS | _DETERMINERS | _PREPOSITIONS | _SUBJECT_PRONOUNS | frozenset(
    """hi hello hey dear thanks thank please yes no okay ok well so then now also maybe sure
    sorry wait actually first second third next finally number step make call send meet set
    book order turn tell ship grab invite email text go come read see let get put take give
    bring keep check ask remember note all both everyone everybody nothing something anything
    one two three four five six seven eight nine ten good great fine right left alright
    awesome perfect cool nice sounds sound unfortunately fortunately luckily hopefully honestly
    basically apparently obviously clearly anyway anyways besides meanwhile otherwise however
    therefore still yet plus instead again lastly overall sadly thankfully today tomorrow
    tonight yesterday later earlier soon once twice bye goodbye cheers regards best kind warm
    sincerely congratulations congrats welcome excuse pardon wow oh ah yay oops ugh yo
    absolutely definitely certainly exactly totally true false wrong correct agreed done
    ready almost perhaps probably possibly really seriously literally frankly personally
    generally usually normally typically ideally initially eventually recently currently
    previously originally unless although though because since while whereas whenever
    wherever until nor everything people things life time work money hope glad happy excited
    sure great love thinking looking looks seems feel think know guess believe wondering
    another other others both either neither each every many much few several some most more
    less little lots quick quickly just only even also too very really quite already always
    never often sometimes rarely here there where what when why how who which whose whom""".split()
)


# --------------------------------------------------------------------------- typed values

_NUMBER_TOKENS = _NUMBER_WORD_SET | frozenset("half quarter dozen noon midnight".split())
_ORDINAL_TOKENS = frozenset(
    """first second third fourth fifth sixth seventh eighth ninth tenth eleventh twelfth
    thirteenth fourteenth fifteenth sixteenth seventeenth eighteenth nineteenth twentieth
    thirtieth""".split()
)
_TIME_SUFFIXES = frozenset("am pm a.m p.m o'clock".split())
_DAY_TOKENS = frozenset(
    "monday tuesday wednesday thursday friday saturday sunday weekend".split())
_MONTH_TOKENS = frozenset(
    "january february march april may june july august september october november december"
    .split()
)
_TEMPORAL_CORE = _DAY_TOKENS | _MONTH_TOKENS | frozenset(
    "today tomorrow tonight yesterday morning afternoon evening night week month year".split()
)
_TEMPORAL_GLUE = frozenset("next this last coming following the day after before early late"
                           .split())


def _is_number(norm: str) -> bool:
    return (norm in _NUMBER_TOKENS or norm in _ORDINAL_TOKENS
            or bool(re.fullmatch(r"\$?\d[\d,.:]*(?:st|nd|rd|th|k|m|%)?", norm)))


def _span(tokens: list[_Tok], start: int, kind: str) -> int:
    """End index of the typed span of ``kind`` starting at ``start`` (start = none)."""
    i = start
    if kind == "number":
        while i < len(tokens):
            norm = tokens[i].norm
            if _is_number(norm) or (i > start and norm in _TIME_SUFFIXES):
                i += 1
            elif (norm == "and" and i > start and i + 1 < len(tokens)
                  and tokens[i + 1].norm in _NUMBER_TOKENS):
                i += 1  # "fifteen hundred and fifty"
            else:
                break
        return i
    core = False
    last_core = start
    while i < len(tokens):
        norm = tokens[i].norm
        previous = tokens[i - 1].norm if i > start else ""
        following = tokens[i + 1].norm if i + 1 < len(tokens) else ""
        if (norm in _TEMPORAL_CORE or (norm == "day" and following in ("after", "before"))
                or (previous == "day" and norm in ("after", "before"))):
            core = True  # "the day after (tomorrow)"
            last_core = i + 1
        elif not (norm in _TEMPORAL_GLUE or (core and _is_number(norm))):
            break
        i += 1
    while i > last_core and tokens[i - 1].norm in _TEMPORAL_GLUE:  # no trailing "the"
        i -= 1
    return i if core else start


def _typed_prefix(tokens: list[_Tok]) -> tuple[str | None, int]:
    if not tokens:
        return None, 0
    for kind in ("temporal", "number"):
        end = _span(tokens, 0, kind)
        if end:
            return kind, end
    return None, 0


def _typed_spans(tokens: list[_Tok], kind: str) -> list[tuple[int, int]]:
    spans = []
    i = 0
    while i < len(tokens):
        end = _span(tokens, i, kind)
        if end > i:
            spans.append((i, end))
            i = end
        else:
            i += 1
    return spans


def _is_proper(token: _Tok) -> bool:
    raw = token.raw.lstrip("\"'(“‘")
    norm = token.norm
    return (bool(raw[:1].isupper()) and norm not in _COMMON_WORDS and norm not in _CLAUSE_VERBS
            and not _is_number(norm) and norm not in _TEMPORAL_CORE)


# --------------------------------------------------------------------------- self-corrections

STRONG, MEDIUM, WEAK, VALUES_ONLY = 3, 2, 1, 0


@dataclass(frozen=True)
class Cue:
    """A spoken self-correction cue: ``text[:start]`` is corrected by ``text[end:]``."""

    start: int
    end: int
    strength: int
    scratch: bool  # "scratch that" family: the corrected words may go entirely
    llm: bool = True  # if the rules can't resolve it, the LLM should look


_CUE_SPECS: tuple[tuple[int, bool, str], ...] = (
    (STRONG, True, r"(?:(?:no|nope|oh|okay|ok|actually|wait|sorry|you\s+know\s+what)[,.!]?\s+)?"
                   r"(?:scratch|strike|delete|erase)\s+(?:that|this)"),
    (STRONG, False, r"(?:no|nope)[,.!]?\s+(?:wait|sorry)(?:[,.!]?\s+(?:i\s+meant|i\s+mean))?"),
    (STRONG, False, r"wait[,.!]?\s+no"),
    (STRONG, False, r"no[,.!]?\s+no(?![,.!]?\s+no\b)"),
    (STRONG, False, r"(?:(?:no|sorry|oops)[,.!]?\s+)?i\s+meant(?:\s+to\s+say)?"),
    (STRONG, False, r"(?:no|sorry)[,.!]?\s+i\s+mean"),
    (STRONG, False, r"or\s+rather|or\s+should\s+i\s+say|i\s+should\s+say"
                    r"|let\s+me\s+rephrase(?:\s+that)?"),
    (STRONG, False, r"(?:actually|no|sorry)[,.!]?\s+(?:make|change)\s+(?:that|it)(?:\s+to)?"),
    (STRONG, False, r"change\s+(?:that|it)\s+to"),
    (STRONG, False, r"actually[,.!]?\s+no|no[,.!]?\s+actually"),
    (STRONG, False, r"correction"),
    (MEDIUM, False, r"i\s+mean|oops|whoops|my\s+bad|nope|sorry|make\s+(?:that|it)"),
    (WEAK, False, r"no|actually"),
)
_CUE_RES = tuple(
    (strength, scratch, re.compile(rf"(?i)(?<![\w'’-])(?:{pattern})(?![\w'’-])"))
    for strength, scratch, pattern in _CUE_SPECS
)
# "no" that is a determiner or an answer, not a correction.
_NO_BEFORE = frozenset(
    """say says said saying answer answered answers is are was were be been being am 's 're
    have has had having with of for by in at on to from there's theres there means mean meant
    or yes the a an and but if so not got get gets do does did will would could should can
    may might must""".split()
)
_NO_IDIOMS = frozenset(
    """problem problems worries worry way idea one body doubt kidding rush hurry thanks thank
    matter longer more less need offense offence wonder clue chance comment pressure big other
    further point reason sense surprise excuse good fun use exception response reply answer
    news change changes issue issues cost charge sweat harm fear end limit question questions
    pun joke sir worries time money luck sign trace access entry smoking parking way""".split()
)
_SORRY_BEFORE = frozenset(
    """i'm im i am so very really truly terribly awfully deeply feel felt feeling say says said
    saying be being been was is are were 're not you're we're they're he's she's it's that's
    look looks looked sound sounds seem seems""".split()
)
_SORRY_AFTER = frozenset(
    """for to about that if but i'm im we're you're they're everyone everybody guys all again
    man mate dear folks sir ma'am bro buddy""".split()
)
# A verb used as a verb ("need to scratch that itch", "can't wait, no ..."): not a cue.
_VERB_CONTEXT = frozenset(
    """to can could will would should must might may 'll i'll we'll you'll they'll gonna wanna
    cannot can't couldn't won't wouldn't didn't did do does don't doesn't i we you they he she
    it just please""".split()
)
_CLAUSE_OPENERS = frozenset(
    """the a an i i'm i'll we we're we'll you you're he she they it it's let's please and so
    then my our""".split()
)
_SCRATCH_OBJECTS = frozenset(
    """itch deal off out from surface record match ball chord pose balance bargain line name word
    item entry part bit one idea plan lottery card""".split()
)
_I_MEAN_BEFORE = frozenset("what that's thats".split())
_I_MEAN_AFTER = frozenset(
    "it that well business no harm anything everything really literally seriously honestly"
    .split()
)
_MAKE_BEFORE = frozenset(
    """can could will would should must might may to 'll i'll we'll you'll they'll gonna cannot
    can't couldn't won't didn't did do does don't doesn't let's please and just really never
    always finally let help try trying tried we you they i""".split()
)
_MAKE_AFTER = frozenset(
    """work works happen happy easy easier better worse possible clear right count through up
    out there home quick quicker short shorter simple simpler sure so more less big bigger
    small smaller fun real official clean safe secure interesting a an""".split()
)
_DISCOURSE_RE = re.compile(
    r"(?i)^(?:(?:okay|ok|so|oh|well|um|uh|hmm|right|alright|yeah|ah)[,.!]?\s+)*")


def _qualify(text: str, m: re.Match[str], strength: int, scratch: bool) -> Cue | None:
    before = text[: m.start()]
    after = text[m.end() :]
    words_before = re.findall(r"[\w'’]+", before.lower())
    words_after = re.findall(r"[\w'’]+", after.lower())
    prev = words_before[-1].replace("’", "'") if words_before else ""
    nxt = words_after[0].replace("’", "'") if words_after else ""
    delimited = not words_before or bool(re.search(r"[,;:.!?…]\s*$", before))
    comma_after = bool(re.match(r"\s*[,:]", after))
    core = " ".join(re.sub(r"[,.!]", " ", m.group(0).lower()).split())
    llm = True
    # "6 AM, no, 7 AM" / "10 am, sorry, 10 pm": a time, not "I am"
    if prev in ("am", "pm") and re.search(r"\d\s*(?:am|pm)\W*$", before.lower()):
        prev = "<time>"
    if core == "no":
        if prev in _NO_BEFORE or nxt in _NO_IDIOMS or nxt in ("", "no"):
            return None
        strength = MEDIUM if delimited or comma_after else WEAK
        llm = strength == MEDIUM
    elif core == "nope":
        if not nxt:
            return None
    elif core == "sorry":
        if prev in _SORRY_BEFORE or nxt in _SORRY_AFTER or not nxt:
            return None
    elif core == "i mean":
        if prev in _I_MEAN_BEFORE or nxt in _I_MEAN_AFTER or not nxt:
            return None
    elif core in ("make it", "make that"):
        if prev in _MAKE_BEFORE or nxt in _MAKE_AFTER:
            return None
        if not delimited:
            strength = VALUES_ONLY  # "order three pizzas make it four"
    elif core == "correction":
        # "Correction, it's 4" / "room 12 correction room 14", not "Correction fluid ..."
        if prev in _DETERMINERS or not (comma_after or (prev and _is_number(prev))
                                        or (words_before and delimited)):
            return None
    elif scratch and core.split()[-2:-1] in (["scratch"], ["strike"], ["delete"], ["erase"]):
        # "we need to scratch that itch", "they want to strike that deal"
        bare = len(core.split()) == 2
        if bare and (prev in _VERB_CONTEXT or nxt in _SCRATCH_OBJECTS):
            return None
        # "delete that file": only a command on its own or before a new clause
        if core.split()[-2] in ("delete", "erase") and nxt and nxt not in _CLAUSE_OPENERS:
            return None
    if core.endswith(" no") and (nxt in _NO_IDIOMS or (core.startswith("wait")
                                                       and prev in _VERB_CONTEXT)):
        return None  # "I can't wait, no kidding"
    elif core == "actually":
        # Values only ("Monday, actually Tuesday"); set off by commas it may still
        # be a change of mind for the LLM ("Invite Alex and Jordan, actually just
        # Jordan"), but "I actually enjoyed it" is just emphasis.
        strength = VALUES_ONLY
        llm = delimited
    return Cue(m.start(), m.end(), strength, scratch, llm)


def find_cues(text: str) -> list[Cue]:
    """Self-correction cues in ``text``, left to right (overlaps: longest wins)."""
    found = []
    for strength, scratch, pattern in _CUE_RES:
        for m in pattern.finditer(text):
            cue = _qualify(text, m, strength, scratch)
            if cue is not None:
                found.append(cue)
    found.sort(key=lambda c: (c.start, -(c.end - c.start), -c.strength))
    cues: list[Cue] = []
    for cue in found:
        if cues and cue.start < cues[-1].end:
            continue
        cues.append(cue)
    return cues


def _leading_cue(sentence: str) -> Cue | None:
    lead = _DISCOURSE_RE.match(sentence)
    offset = lead.end() if lead else 0
    for cue in find_cues(sentence):
        if cue.start <= offset:
            return cue
        break
    return None


_WINDOW = 10  # how far back (in words) a correction may reach
_NOT_CONTENT = frozenset(
    "no nope oh okay ok so well um uh wait sorry yes yeah actually hmm ah right alright".split())


def has_content(text: str) -> bool:
    """``text`` says something beyond cue/discourse words ("No no" has nothing to correct)."""
    return any(t.norm not in _NOT_CONTENT for t in _tokens(text))


# Verbs that open dictated instructions ("call me tonight", "turn right").
_CLAUSE_VERBS = frozenset(
    """call send email text meet turn go come update see take make get put give bring book order
    buy check ask tell let keep try use add remove delete move set change fix write read open
    close start stop run schedule cancel invite ship pay review finish submit share reply
    forward print sign pick leave wait plan cook clean wash drive fly walk look listen think
    remember forget find follow join help show play watch reserve rent text grab drop post
    upload download install restart reboot push pull merge deploy test build""".split()
)


def _is_clause(tokens: list[_Tok]) -> bool:
    """B starts a clause of its own ("I'll call you tonight", "turn right"), not just
    a phrase ("room 205", "tea for me", "the park")."""
    first = tokens[0].norm
    return (first in _SUBJECT_PRONOUNS or first in _AUXILIARIES or first in _CLAUSE_VERBS
            or first in ("please", "what", "why", "how", "where", "when", "who"))


def is_clause(text: str) -> bool:
    """``text`` opens a clause of its own rather than finishing a phrase
    ("I'll call you tonight" vs. "tonight" / "at the park")."""
    tokens = _tokens(text)
    return bool(tokens) and _is_clause(tokens)


def _end_punct(text: str) -> str:
    m = re.search(r"[.!?…]+$", text.rstrip())
    return m.group(0) if m else ""


def _substitute(a: str, a_tokens: list[_Tok], start: int, end: int, b: str) -> str:
    """Replace tokens ``start:end`` of ``a`` with the value ``b``, keeping a's tail."""
    value = b.strip().rstrip(".!?…,;:")
    tail = a[a_tokens[end - 1].end :]
    stripped_tail = re.sub(r"[.!?…]+$", "", tail.rstrip())
    return (a[: a_tokens[start].start] + value + stripped_tail + _end_punct(b)).strip()


def _splice(a: str, a_tokens: list[_Tok], start: int, b: str) -> str:
    return (a[: a_tokens[start].start] + b.strip()).strip()


# A comment on the new value, not a restatement: "No, Saturday works better".
_COMMENT_VERBS = frozenset(
    """works work is would will might should could seems sounds looks fits suits better
    makes""".split()
)


def _typed(a: str, at: list[_Tok], b: str, bt: list[_Tok]) -> str | None:
    """Same kind of value: numbers/times ("at 3, no, 4"), days and dates
    ("Monday, no, Tuesday"), times of day ("tomorrow morning no tonight")."""
    kind, length = _typed_prefix(bt)
    if kind is None:
        return None
    first = max(0, len(at) - _WINDOW)
    for start, end in reversed(_typed_spans(at, kind)):
        if end <= first:
            break
        rest = bt[length:]
        unit = int(bool(rest) and end < len(at) and rest[0].norm == at[end].norm
                    and rest[0].norm not in _FUNCTION_WORDS)
        if len(rest) == unit:  # just the value (and its unit): swap it in, keep the rest
            return _substitute(a, at, start, end + unit, b)
        if rest[unit].norm in _COMMENT_VERBS:  # "No, Saturday works better."
            return _substitute(a, at, start, end + unit,
                               b[: bt[length + unit - 1].end] + _end_punct(b))
        return _splice(a, at, start, b)
    return None


_SKELETON = frozenset("it's it is it'll be that's make that the".split())


def _skeleton(a: str, at: list[_Tok], b: str, bt: list[_Tok]) -> str | None:
    """"The meeting is at three. No wait, it's at four." -> "... at four"."""
    i = 0
    while i < len(bt) and bt[i].norm in _SKELETON:
        i += 1
    preposition = ""
    if i < len(bt) and bt[i].norm in _PREPOSITIONS:
        preposition = bt[i].norm
        i += 1
    if i == 0 or i >= len(bt):
        return None
    kind, length = _typed_prefix(bt[i:])
    if kind is None or i + length != len(bt):
        return None
    for start, end in reversed(_typed_spans(at, kind)):
        if end <= len(at) - _WINDOW:
            break
        if preposition and (start == 0 or at[start - 1].norm != preposition):
            continue
        value = b[bt[i].start :]
        return _substitute(a, at, start, end, value)
    return None


def _proper_swap(a: str, at: list[_Tok], b: str, bt: list[_Tok], strength: int) -> str | None:
    """A name for a name: "Call Ravi tomorrow. I meant Rohan." -> "Call Rohan tomorrow"."""
    if not _is_proper(bt[0]):
        return None
    length = 1
    while length < len(bt) and _is_proper(bt[length]):
        length += 1
    rest = bt[length:]
    if rest and not (rest[0].norm in _JOINERS or strength >= MEDIUM):
        return None
    for start in range(len(at) - 1, max(0, len(at) - _WINDOW) - 1, -1):
        if start == 0 or not _is_proper(at[start]):
            continue
        end = start + 1
        while start > 1 and _is_proper(at[start - 1]):
            start -= 1
        if not rest:
            return _substitute(a, at, start, end, b)
        return _splice(a, at, start, b)
    return None


def _restart(a: str, at: list[_Tok], b: str, bt: list[_Tok], strength: int) -> str | None:
    """B restarts from a word near the end of A ("at the bakery, no, the museum")."""
    clause = _is_clause(bt)
    if strength == WEAK and (clause or len(bt) > 6
                             or any(t.norm in _AUXILIARIES for t in bt)):
        return None  # bare "no": only short, verb-less repairs ("no the park")
    n = len(at)
    for i in range(n - 1, max(-1, n - 1 - _WINDOW), -1):
        if at[i].norm != bt[0].norm:
            continue
        overlap = 1
        while (i + overlap < n and overlap < len(bt)
               and at[i + overlap].norm == bt[overlap].norm):
            overlap += 1
        if overlap == len(bt) and i + overlap == n:
            return None  # B repeats A's end: nothing changes
        previous = at[i - 1].norm if i else ""
        if clause:
            ok = (overlap >= 3 or (overlap >= 2 and strength >= MEDIUM)
                  or (strength == STRONG and (i == 0 or previous in _CONJUNCTIONS)))
        elif strength == WEAK:
            ok = bt[0].norm in _DETERMINERS | _PREPOSITIONS and i >= n - 5
        else:
            ok = True
        if ok:
            return _splice(a, at, i, b)
    return None


def _overlay(a: str, at: list[_Tok], b: str, bt: list[_Tok]) -> str | None:
    """B restates a phrase of A with one word changed, A's rest still holds:
    "Tell Mom I'll be late. Sorry, tell Dad." -> "Tell Dad I'll be late"."""
    k = len(bt)
    if not 2 <= k <= 4 or bt[0].norm in _FUNCTION_WORDS or bt[0].norm in _SUBJECT_PRONOUNS:
        return None
    if any(t.norm in _AUXILIARIES for t in bt):
        return None
    for i in range(len(at) - k, max(-1, len(at) - _WINDOW - 1), -1):
        if at[i].norm != bt[0].norm:
            continue
        diffs = [j for j in range(1, k) if at[i + j].norm != bt[j].norm]
        if (len(diffs) != 1 or bt[diffs[0]].norm in _FUNCTION_WORDS
                or at[i + diffs[0]].norm in _FUNCTION_WORDS):
            continue
        # A's rest must stand apart ("... at the light", "... I'll be late"), not
        # finish the replaced phrase ("see how it plays | out").
        rest = at[i + k].norm if i + k < len(at) else ""
        if rest and not (rest in _PREPOSITIONS or rest in _SUBJECT_PRONOUNS
                         or rest in _CONJUNCTIONS or rest in _TEMPORAL_CORE
                         or rest in ("that", "which", "who", "because", "when", "if")):
            continue
        return _substitute(a, at, i, i + k, b)
    return None


def _suffix(a: str, at: list[_Tok], b: str, bt: list[_Tok]) -> str | None:
    """B restates A's end with one change: "Email Mike, no, text Mike"."""
    k = len(bt)
    if (2 <= k <= min(4, len(at)) and bt[-1].norm == at[-1].norm
            and bt[-1].norm not in _FUNCTION_WORDS
            and [t.norm for t in bt] != [t.norm for t in at[-k:]]):
        return _splice(a, at, len(at) - k, b)
    return None


def _swap_last(a: str, at: list[_Tok], b: str, bt: list[_Tok]) -> str | None:
    """One content word for another ("grab coffee, no, tea for me")."""
    last, first = at[-1].norm, bt[0].norm
    closes = bt[0].raw.endswith((",", ".", "?", "!"))
    if (last not in _FUNCTION_WORDS and first not in _FUNCTION_WORDS
            and (len(bt) == 1 or closes or bt[1].norm in _JOINERS)):
        return _splice(a, at, len(at) - 1, b)
    return None


def _drop_echo_comma(text: str, b: str) -> str:
    """"Tell Priya, I mean Ananya, that ..." -> no comma after the repair."""
    m = re.match(r"\s*\S+?(?:\s+\S+?)?,\s+([\w'’]+)", b)
    if m and m.group(1).lower() not in ("and", "but", "or", "so", "because", "since", "which",
                                       "who", "although", "though", "while", "unless", "if",
                                       "when", "then"):
        head = b[: m.start(1)].rstrip()
        fixed = head[:-1] + " " if head.endswith(",") else None
        if fixed is not None and text.endswith(b.strip()):
            return text[: len(text) - len(b.strip())] + fixed + b[m.start(1) :].strip()
    return text


def correct(a: str, b: str, strength: int = STRONG, *, comma_cue: bool = False) -> str | None:
    """Replace the corrected end of ``a`` with the repair ``b``; None when unsure.

    ``strength`` limits how much evidence is needed: a bare "no" only swaps
    values of the same kind or repeats a short phrase; "no wait" / "I meant"
    may also restart a clause or swap one word for another.
    """
    a = a.rstrip()
    b = b.strip().lstrip(",.;: ")
    a_body = re.sub(r"[.!?…,;:]+$", "", a)
    at, bt = _tokens(a_body), _tokens(b)
    if not at or not bt:
        return None
    rules: list[Callable[[], str | None]] = [
        lambda: _typed(a_body, at, b, bt),
        lambda: _skeleton(a_body, at, b, bt),
    ]
    if strength >= WEAK:
        rules += [lambda: _proper_swap(a_body, at, b, bt, strength),
                  lambda: _overlay(a_body, at, b, bt),
                  lambda: _restart(a_body, at, b, bt, strength),
                  lambda: _suffix(a_body, at, b, bt)]
    if strength >= MEDIUM:
        rules += [lambda: _swap_last(a_body, at, b, bt)]
    for rule in rules:
        result = rule()
        if result is not None:
            return _drop_echo_comma(result, b) if comma_cue else result
    return None


# Cues that mean nothing when nothing came before them in the dictation.
_DROP_AT_START_RE = re.compile(
    r"(?i)^(?:(?:okay|ok|so|oh|well|um|uh)[,.!]?\s+)*(?:no[,.!]?\s+wait|wait[,.!]?\s+no"
    r"|no[,.!]?\s+sorry|sorry[,.!]?\s+i\s+meant|i\s+meant)\b"
)


def _keep_question_mark(previous: str, result: str) -> str:
    if previous.rstrip().endswith(("?", "!")):
        return re.sub(r"[.!?…]*$", "", result.rstrip()) + previous.rstrip()[-1]
    return result


def resolve_corrections(text: str) -> str:
    """Apply confidently-resolvable spoken self-corrections."""
    out: list[str] = []
    for sentence in split_sentences(text):
        if sentence.startswith("\n"):
            out.append(sentence)
            continue
        current = sentence
        # Across sentences: "... on the 12th. Actually, make it the 14th."
        lead = _leading_cue(current)
        if lead is not None:
            repair = current[lead.end :].lstrip(" ,.;:!")
            previous = out[-1] if out and not out[-1].startswith("\n") else None
            if repair and previous is not None:
                answer = (previous.rstrip().endswith("?") and lead.strength < STRONG
                          and _tokens(repair)[0].norm in _SUBJECT_PRONOUNS)
                replaced = None if answer else correct(previous, repair, lead.strength)
                if replaced is not None:
                    out[-1] = _keep_question_mark(previous, replaced)
                    continue
                if not answer and (lead.scratch or (lead.strength == STRONG
                                                    and _is_clause(_tokens(repair)))):
                    out.pop()  # "Scratch that, call me tonight": the previous sentence goes
                    current = upper_first(repair)
            elif repair and _DROP_AT_START_RE.match(current):
                current = upper_first(repair)  # "No wait, let's ..." with nothing before
            elif not repair and lead.scratch and previous is not None:
                out.pop()
                continue
        # Within a sentence: "... at 3, no wait, 4 p.m. ..."
        searched = 0
        for _ in range(6):
            cue = next((c for c in find_cues(current)
                        if c.start >= searched and has_content(current[: c.start])), None)
            if cue is None:
                break
            a = current[: cue.start].rstrip(" ,;:")
            b = current[cue.end :].lstrip(" ,.;:!")
            if not re.search(r"\w", b):
                if cue.scratch:
                    current = ""  # "... scratch that": the sentence goes
                break
            comma_cue = bool(re.search(r",\s*$", current[: cue.start]))
            replaced = correct(a, b, cue.strength, comma_cue=comma_cue)
            if replaced is None and (cue.scratch or (cue.strength == STRONG
                                                     and _is_clause(_tokens(b))
                                                     and len(_tokens(b)) >= 2)):
                replaced = upper_first(b)
            if replaced is None:
                searched = cue.end
                continue
            current = replaced
        if current.strip():
            out.append(current)
    result = join_sentences(out)
    return _capitalize(result) if result != text else text


def _answers_question(text: str, cue: Cue) -> bool:
    """"Did you finish it? No, I didn't." - an answer, not a correction."""
    after = _tokens(text[cue.end :])
    return (cue.strength < STRONG and text[: cue.start].rstrip().endswith("?")
            and bool(after) and after[0].norm in _SUBJECT_PRONOUNS)


def has_correction_cue(text: str, language: str = "en") -> bool:
    """A correction the rules could not resolve (worth asking the LLM)."""
    if language == "en":
        return any(cue.llm and has_content(text[: cue.start]) and not _answers_question(text, cue)
                   for cue in find_cues(text))
    pattern = _language_cue_re("correction", language)
    return bool(pattern and pattern.search(text))


def starts_with_correction_cue(text: str) -> bool:
    """The fragment corrects what came before ("No, Tuesday", "Sorry, I meant 7")."""
    return _leading_cue(text.strip()) is not None


_DANGLING_CUE_RE = re.compile(
    r"(?i)(?:^|[\s,])(?:no(?:[,.]?\s+wait)?|wait[,.]?\s+no|sorry|i\s+mean|i\s+meant"
    r"|or\s+rather|(?:no[,.]?\s+)?scratch\s+that|make\s+(?:that|it)|correction)[,.!:]?\s*$"
)


def ends_with_correction_cue(text: str) -> bool:
    """The fragment stops right after a cue: the repair follows after the pause."""
    return bool(_DANGLING_CUE_RE.search(text))


# --------------------------------------------------------------------------- lists

_ORDINALS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7,
    "eighth": 8, "ninth": 9, "tenth": 10,
}
_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10,
}
_ORD_ALT = "|".join(_ORDINALS)
_CARD_ALT = "|".join(_NUMBER_WORDS)
_RANKED_RE = re.compile(
    rf"(?i)(?<![\w'’-])(?:(?P<ord>(?:{_ORD_ALT})(?:ly)?)(?P<ofall>\s+of\s+all)?"
    rf"(?:\s+(?P<ordnoun>step|item|point|thing))?"
    rf"|(?:number|step|item|point)\s+(?P<num>{_CARD_ALT}|\d+)"
    rf"|(?P<card>{_CARD_ALT}))(?![\w'’-])[,:.]?"
)
_CONTINUATION_RE = re.compile(
    r"(?i)(?<![\w'’-])(?P<and>and\s+)?(?P<word>next|then|after\s+that|finally|lastly"
    r"|last\s+but\s+not\s+least|last(?=\s*,))(?![\w'’-])[,:]?"
)
# Nouns after an unpunctuated ordinal make it an adjective: "first time", "second
# floor". (Words that are often verbs - "book", "try", "name" - are left out:
# "first book the flights".)
_RANK_NOUNS = frozenset(
    """time times line lines thing things place places floor floors half round rounds
    attempt attempts option options version versions choice choices priority priorities prize
    prizes day days week weeks month months year years impression impressions person people
    quarter quarters class grade aid base gear chapter chapters section sections part parts
    term terms edition generation cousin cousins language languages degree one ones reason
    reasons problem problems lady world anniversary birthday semester period phase team
    episode season baby child son daughter and or""".split()
)
_LIST_INTRO_WORDS = frozenset(
    """things steps items points reasons ways tasks goals options priorities rules questions
    issues ideas tips are is include includes following need needs list""".split()
)
_PLURAL_EXCEPTIONS = frozenset("is was has does this his its us yes plus bus gas less".split())


@dataclass(frozen=True)
class _Marker:
    start: int
    end: int  # where the item text begins
    rank: int | None  # None: "next" / "then" / "finally"
    kind: str  # "ord" | "num" | "card" | "cont"
    delimited: bool


def _marker_delimited(text: str, start: int, end: int) -> bool:
    before = text[:start].rstrip()
    return (not before or before.endswith((".", ";", ":", ",", "!", "?", "…", "\n"))
            or bool(re.search(r"(?i)\b(?:and|then)$", before))
            or bool(re.match(r"[,:.]", text[end - 1 : end])))


def _list_markers(text: str) -> list[_Marker]:
    markers = []
    for m in _RANKED_RE.finditer(text):
        before = text[: m.start()].rstrip()
        words_before = re.findall(r"[\w'’]+", before.lower())
        # Only a word in the same clause counts: "Do this. Second, ..." is a marker.
        previous = words_before[-1] if words_before and before[-1:].isalnum() else ""
        following = re.findall(r"[\w'’]+", text[m.end() :].lower())
        nxt = following[0] if following else ""
        if previous in _DETERMINERS or previous in ("at", "which", "whose", "what"):
            continue  # "the first time", "our number one priority", "at first"
        if m.group("ord"):
            if nxt in _RANK_NOUNS and not m.group("ordnoun") and m.group(0)[-1] not in ",:.":
                continue
            word = m.group("ord").lower()
            rank, kind = _ORDINALS[word[:-2] if word.endswith("ly") else word], "ord"
        elif m.group("num"):
            token = m.group("num").lower()
            rank = int(token) if token.isdigit() else _NUMBER_WORDS[token]
            kind = "num"
        else:
            if (previous in ("no", "any", "every", "each", "some", "the", "of", "or", "than")
                    or nxt in ("of", "or", "more", "another", "by", "and", "")
                    or (nxt.endswith("s") and nxt not in _PLURAL_EXCEPTIONS)):
                continue  # "no one", "one of", "two cats": quantities, not markers
            rank, kind = _NUMBER_WORDS[m.group("card").lower()], "card"
        end = m.end() + len(text[m.end() :]) - len(text[m.end() :].lstrip())
        markers.append(_Marker(m.start(), end, rank, kind,
                               _marker_delimited(text, m.start(), m.end())))
    for m in _CONTINUATION_RE.finditer(text):
        word = m.group("word").lower()
        before = text[: m.start()].rstrip()
        if word.startswith(("finally", "last")):
            ok = (bool(m.group("and")) or not before
                  or before.endswith((".", ";", "!", "?", ",", ":", "\n")))
        else:
            ok = not before or before.endswith((".", ";", "!", "?", "\n"))
        if ok:
            end = m.end() + len(text[m.end() :]) - len(text[m.end() :].lstrip())
            markers.append(_Marker(m.start(), end, None, "cont", True))
    return sorted(markers, key=lambda marker: marker.start)


def _select_markers(text: str, markers: list[_Marker]) -> list[_Marker]:
    chosen: list[_Marker] = []
    expected = 1
    mode = ""
    for index, marker in enumerate(markers):
        if chosen and marker.start < chosen[-1].end:
            continue
        if marker.kind == "cont":
            nxt = markers[index + 1] if index + 1 < len(markers) else None
            if nxt is not None and nxt.rank == expected and nxt.start <= marker.end + 1:
                continue  # "and finally number three": the number is the marker
            if chosen:
                chosen.append(marker)
                expected += 1
            continue
        if marker.rank != expected:
            continue
        if not chosen:
            mode = marker.kind
        elif marker.kind == "card" and mode not in ("card", "num"):
            continue
        chosen.append(marker)
        expected += 1
    ranked = [m for m in chosen if m.rank is not None]
    if not ranked:
        return []
    if mode == "card" and len(ranked) < 3:
        return []
    first = ranked[0]
    intro = re.findall(r"[\w'’]+", text[: first.start].lower())
    opens = first.delimited or bool(intro and intro[-1] in _LIST_INTRO_WORDS)
    if len(ranked) < 3 and not opens:
        return []
    if len(chosen) < 2 or (len(ranked) < 2 and len(chosen) < 3):
        return []
    return chosen


_ITEM_TAIL_RE = re.compile(
    r"(?i)(?:[.,;:]+|(?:,?\s+|^)(?:and|then|also|next|finally|lastly)(?=[.,;:]*$))+$")


def _clean_item(item: str) -> str:
    item = item.strip().strip(",;:").strip()
    item = re.sub(r"^(?:and|then|also|of\s+all)[,]?\s+", "", item, flags=re.I)
    # "Step one is to unplug the router" -> "Unplug the router"
    item = re.sub(r"(?i)^(?:is|was|would\s+be|will\s+be|should\s+be)(?:\s+to)?\s+(?=\w)", "", item)
    previous = None
    while previous != item:  # "a car and finally", "the hotel. And"
        previous = item
        item = _ITEM_TAIL_RE.sub("", item).strip()
    if item and item[0].islower() and not any(c.isupper() for c in item.split()[0][1:]):
        item = item[0].upper() + item[1:]
    return item


def _assemble_list(intro: str, lines: list[str], outro: str = "") -> str:
    parts = []
    intro = re.sub(r"(?i)[\s,.;:]+$|\s+and$", "", intro.strip())
    if intro:
        parts.append(intro + ":")
    parts.extend(lines)
    text = "\n".join(parts)
    if outro:
        text += "\n" + outro.strip()
    return text


_BULLET_RE = re.compile(r"(?i)(?:^|(?<=[\s.,;:]))bullet(?:\s+point)?[,:.]?\s+")


def _marker_list(text: str) -> str | None:
    chosen = _select_markers(text, _list_markers(text))
    if not chosen:
        return None
    items = [text[m.end : (chosen[i + 1].start if i + 1 < len(chosen) else len(text))]
             for i, m in enumerate(chosen)]
    outro = ""
    last_parts = split_sentences(items[-1].strip())
    if len(last_parts) > 1:
        items[-1] = last_parts[0]
        outro = join_sentences(last_parts[1:])
    cleaned = [_clean_item(item) for item in items]
    while cleaned and not cleaned[-1]:
        cleaned.pop()
    if len(cleaned) < 2 or not all(cleaned):
        return None  # an empty item: "the first and second floors"
    numbered = [f"{n}. {item}" for n, item in enumerate(cleaned, start=1)]
    return _assemble_list(text[: chosen[0].start], numbered, outro)


# Implicit lists: "we need four things: the laptop, the charger, the mouse and the bag".
_COUNT_WORDS = {**_NUMBER_WORDS, **{str(n): n for n in range(2, 11)}}
_LIST_NOUNS = (r"things|items|points|steps|reasons|ways|tasks|goals|options|priorities|rules"
               r"|questions|issues|ideas|tips|benefits|problems|features|topics|changes"
               r"|requirements|ingredients|stops|places|names|products|tools|apps|parts|stuff")
_COUNT_RE = re.compile(
    rf"(?i)\b(?P<n>{'|'.join(_COUNT_WORDS)})\s+(?:(?:main|key|important|big|simple|quick|more"
    rf"|other|different|basic|major|small|new|little)\s+)?(?:{_LIST_NOUNS})\b"
)
_LIST_CUE_RE = re.compile(
    r"(?i)\b(?:make\s+a\s+list|(?:shopping|grocery|to-?do|packing|reading|guest)\s+list"
    r"|the\s+following|here(?:'s|\s+is)\s+a\s+list)\b"
)
_ITEM_SPLIT_RE = re.compile(r"\s*,\s*(?:and\s+|or\s+)?|\s+and\s+|\s+or\s+")
_INTRO_VERB_RE = re.compile(
    r"(?i)^(?P<intro>.*\b(?:need|needs|are|is|include|includes|including|buy|get|bring|pack"
    r"|want|have|has))\s+(?P<item>\S.*)$"
)
_MAX_ITEM_WORDS = 6


_ITEM_DETERMINERS = frozenset("the a an my our your his her their some".split())


def _split_at_determiners(text: str) -> list[str]:
    """"the laptop the charger and the bag" -> three items (empty if unclear)."""
    words = text.split()
    if not words or words[0].lower() not in _ITEM_DETERMINERS:
        return []
    items: list[list[str]] = []
    for i, word in enumerate(words):
        lowered = word.lower().strip(".,")
        if lowered in _ITEM_DETERMINERS:
            items.append([word])
        elif lowered == "and" and i + 1 < len(words) and words[i + 1].lower() in _ITEM_DETERMINERS:
            continue
        else:
            items[-1].append(word)
    return [" ".join(item).strip(" .,:;") for item in items]


def _implicit_list(sentence: str) -> str | None:
    body = sentence.rstrip(" .!")
    count = _COUNT_RE.search(body)
    cue = None if count else _LIST_CUE_RE.search(body)
    anchor = count or cue
    if anchor is None:
        return None
    intro, rest = body[: anchor.end()], body[anchor.end() :]
    rest = re.sub(r"(?i)^\s*(?:[:,]|of\b|are\b|is\b|which are\b|namely\b)?\s*", "", rest)
    if "," in rest:
        segments = [s.strip(" .,:;") for s in _ITEM_SPLIT_RE.split(rest) if s.strip(" .,:;")]
    elif count and re.search(r"(?i)\s(?:and|or)\s", rest):
        # no commas: "four things the laptop the charger the mouse and the bag"
        segments = _split_at_determiners(rest)
        words = [w.strip(".,") for w in rest.split() if w.lower().strip(".,") not in ("and", "or")]
        if not segments and len(words) == _COUNT_WORDS[count.group("n").lower()]:
            segments = words  # "three reasons: cost speed and quality"
    else:
        return None
    if count:
        wanted = _COUNT_WORDS[count.group("n").lower()]
        if len(segments) == wanted + 1:  # "two things I love, coffee and music"
            intro, segments = f"{intro} {segments[0]}", segments[1:]
        if len(segments) != wanted:
            return None
    else:
        if len(segments) < 3:
            return None
        first = segments[0]
        if len(first.split()) > 3:  # "of what all we need the flour, butter, ..."
            split = _INTRO_VERB_RE.match(first)
            if split is None or len(split.group("item").split()) > 3:
                return None
            intro, segments[0] = f"{intro} {split.group('intro')}", split.group("item")
    if any(len(s.split()) > _MAX_ITEM_WORDS for s in segments):
        return None
    items = [_clean_item(s) for s in segments]
    if not all(items):
        return None
    return _assemble_list(intro, [f"{n}. {item}" for n, item in enumerate(items, start=1)])


def _format_block(text: str) -> str | None:
    bullets = list(_BULLET_RE.finditer(text))
    if len(bullets) >= 2:
        intro = text[: bullets[0].start()].strip()
        bullet_items = [
            _clean_item(text[m.end() : (bullets[i + 1].start() if i + 1 < len(bullets) else None)])
            for i, m in enumerate(bullets)
        ]
        return _assemble_list(intro, [f"- {it}" for it in bullet_items if it])
    listed = _marker_list(text)
    if listed is not None:
        return listed
    sentences = split_sentences(text)
    for index, sentence in enumerate(sentences):
        implicit = _implicit_list(sentence)
        if implicit is not None:
            before = join_sentences(sentences[:index])
            after = join_sentences(sentences[index + 1 :])
            parts = [p for p in (before, implicit, after) if p]
            return "\n".join(parts)
    return None


def format_spoken_list(text: str) -> str | None:
    """Turn enumerated speech into numbered or bulleted lists.

    Each line (or paragraph) is handled on its own. Returns ``None`` when
    nothing looks like a list: fewer than two ordinal markers in sequence,
    no count-announced enumeration and no "bullet point" commands.
    """
    lines = text.split("\n")
    changed = False
    for index, line in enumerate(lines):
        if not line.strip() or re.match(r"^\s*(?:\d+[.)]|[-*•])\s", line):
            continue
        formatted = _format_block(line)
        if formatted is not None:
            lines[index] = formatted
            changed = True
    return "\n".join(lines) if changed else None


def _open_list(text: str) -> tuple[int, bool] | None:
    """(next expected rank, last item still empty) when ``text`` ends inside a list."""
    chosen = _select_markers(text, _list_markers(text))
    if not chosen:
        firsts = [m for m in _list_markers(text) if m.rank == 1 and m.kind != "card"]
        if not firsts:
            return None
        chosen = [firsts[0]]
    item_pending = not text[chosen[-1].end :].strip(" ,.;:")
    return len(chosen) + 1, item_pending  # every marker (also "then"/"finally") is one item


def has_list_cue(text: str) -> bool:
    return bool(_select_markers(text, _list_markers(text))) or len(_BULLET_RE.findall(text)) >= 2


def starts_list_item(text: str) -> bool:
    text = text.strip()
    return any(m.start == 0 for m in _list_markers(text)) or bool(_BULLET_RE.match(text))


def starts_first_list_item(text: str) -> bool:
    """True for "First, ...", "Number one ...", "Step 1: ..." and "Bullet point ..."."""
    text = text.strip()
    if _BULLET_RE.match(text):
        return True
    return any(m.start == 0 and m.rank == 1 and m.kind in ("ord", "num")
               for m in _list_markers(text))


def ends_list_item(text: str) -> bool:
    """``text`` ends inside a list, after an item that already has words."""
    state = _open_list(text)
    return state is not None and not state[1] and has_list_cue(text)


def continues_list(unit_text: str, fragment: str) -> bool:
    """A new fragment belongs to the list that ``unit_text`` ends with."""
    state = _open_list(unit_text)
    if state is None:
        return False
    expected, item_pending = state
    if item_pending:
        return True
    return any(m.rank in (expected, None) and m.start <= 4
               for m in _list_markers(fragment.strip()))


# --------------------------------------------------------------------------- cue detection

_SOFT_FILLER_RE = re.compile(
    r"(?ix)(?:^|[,.!?]\s*)(?:like|you\s+know|i\s+mean|kind\s+of|sort\s+of|basically|"
    r"literally|you\s+see|okay\s+so|so\s+yeah|well)\s*,"
    r"|,\s*(?:like|you\s+know|i\s+mean|basically|literally|right)\s*[,.?]"
    # without commas (recognizers often drop them): "we should you know focus",
    # "I was like really tired"
    r"|(?<!\bdo\s)(?<!\bdid\s)(?<!\bif\s)(?<!\bas\s)(?<!n't\s)\byou\s+know\s+"
    r"(?!what\b|how\b|that\b|the\b|where\b|why\b|when\b|who\b|if\b|whether\b|about\b|it\b|him\b"
    r"|her\b|them\b|me\b|us\b|this\b|my\b|your\b)"
    r"|\b(?:was|were|is|am|are|['’]s|['’]m|['’]re)\s+like\s+(?:really|so|totally|super|very"
    r"|just|kind|sort|literally)\b|\blike\s+you\s+know\b|\bbasically\b"
)


# Other languages: phrases that signal a self-correction, and comma-delimited
# filler words. They only decide whether the LLM looks at a sentence; plain
# sentences keep the recognizer's text.
_CORRECTION_CUES_BY_LANGUAGE: dict[str, str] = {
    "es": r"no,?\s+(?:mejor|espera|perdón)|quiero decir|mejor dicho|perdón|digo",
    "fr": r"non,?\s+(?:attends|pardon)|je veux dire|ou plutôt|pardon|je me corrige",
    "de": r"nein,?\s+(?:warte|sorry)|ich meine|besser gesagt|beziehungsweise|korrektur",
    "it": r"no,?\s+(?:aspetta|scusa)|volevo dire|cioè|anzi",
    "pt": r"não,?\s+(?:espera|desculpa)|quer dizer|ou melhor|aliás",
    "nl": r"nee,?\s+(?:wacht|sorry)|ik bedoel|beter gezegd|of liever",
    "hi": r"मतलब|नहीं नहीं|नहीं,|सॉरी|यानी",
}
_SOFT_FILLERS_BY_LANGUAGE: dict[str, str] = {
    "es": r"o sea|este|pues|bueno|tipo|en plan",
    "fr": r"genre|du coup|en fait|bah|ben|tu vois",
    "de": r"also|halt|quasi|sozusagen|irgendwie|weißt du",
    "it": r"tipo|praticamente|diciamo|insomma",
    "pt": r"tipo|né|sabe",
    "nl": r"zeg maar|eigenlijk|weet je",
    "hi": r"वो|यार",
}
_LANGUAGE_CUE_RES: dict[tuple[str, str], re.Pattern[str]] = {}


def _language_cue_re(kind: str, language: str) -> re.Pattern[str] | None:
    key = (kind, language)
    if key not in _LANGUAGE_CUE_RES:
        table = _CORRECTION_CUES_BY_LANGUAGE if kind == "correction" else _SOFT_FILLERS_BY_LANGUAGE
        words = table.get(language)
        if words is None:
            return None
        if kind == "correction":
            pattern = rf"(?i)(?<![\w'])(?:{words})(?![\w'])"
        else:  # only comma-delimited, like the English soft fillers
            pattern = rf"(?i)(?:^|[,.!?]\s*)(?:{words})\s*,|,\s*(?:{words})\s*[,.?]"
        _LANGUAGE_CUE_RES[key] = re.compile(pattern)
    return _LANGUAGE_CUE_RES[key]


def has_soft_fillers(text: str, language: str = "en") -> bool:
    if language == "en":
        return bool(_SOFT_FILLER_RE.search(text))
    pattern = _language_cue_re("filler", language)
    return bool(pattern and pattern.search(text))


_RESTART_RE = re.compile(
    rf"(?i)\b((?:{_WORD}\s+){{1,3}}{_WORD})\s+(?P<gap>(?:{_WORD}\s+){{1,4}}?)\1\b")


def restart_spans(text: str) -> list[tuple[int, int]]:
    """Restarted phrases ("I think we should I think we must ..."): the (start, end)
    of each repeated phrase - what precedes it was abandoned."""
    spans = []
    for m in _RESTART_RE.finditer(text):
        gap = m.group("gap").split()
        phrase = m.group(1).lower().split()
        if (gap and gap[-1].lower() not in _CONJUNCTIONS
                and not {"bullet", "point", "number", "step", "item"} & set(phrase)):
            spans.append((m.end("gap"), m.end()))
    return spans


def has_restart(text: str) -> bool:
    """A phrase restarted with a change: "I think we should, I think we must ..."."""
    return bool(restart_spans(text))


# --------------------------------------------------------------------------- times

_HOURS = {w: i for i, w in enumerate(
    "one two three four five six seven eight nine ten eleven twelve".split(), start=1)}
_MINUTES = {
    "oh five": 5, "o five": 5, "ten": 10, "fifteen": 15, "twenty": 20, "twenty five": 25,
    "thirty": 30, "thirty five": 35, "forty": 40, "forty five": 45, "fifty": 50, "fifty five": 55,
}
_HOUR_ALT = "|".join(sorted(_HOURS, key=len, reverse=True))
_MINUTE_ALT = "|".join(
    k.replace(" ", r"[\s-]+") for k in sorted(_MINUTES, key=len, reverse=True)
)
_TIME_RE = re.compile(
    rf"(?i)\b(?P<h>{_HOUR_ALT}|1[0-2]|[1-9])(?:[\s-]+(?P<m>{_MINUTE_ALT}))?\s*"
    r"(?P<suffix>a\.\s?m\.|p\.\s?m\.|am\b|pm\b|o'?clock\b)"
)


def normalize_times(text: str) -> str:
    """"four thirty PM" -> "4:30 PM", "ten a.m." -> "10 a.m.", "nine o'clock" -> "9 o'clock"."""

    def repl(m: re.Match[str]) -> str:
        raw_hour = m.group("h").lower()
        hour = _HOURS.get(raw_hour) or int(raw_hour)
        minute = None
        if m.group("m"):
            minute = _MINUTES[re.sub(r"[\s-]+", " ", m.group("m").lower())]
        clock = f"{hour}:{minute:02d}" if minute is not None else f"{hour}"
        suffix = m.group("suffix").lower().replace(" ", "")
        if "clock" in suffix:
            return clock if minute is not None else f"{hour} o'clock"
        if "." in suffix:
            return f"{clock} {'a.m.' if suffix.startswith('a') else 'p.m.'}"
        return f"{clock} {'AM' if suffix.startswith('a') else 'PM'}"

    return _TIME_RE.sub(repl, text)


# Words a finished sentence practically never ends with: a pause after them is
# a pause mid-sentence, whatever the case of the next word.
_NON_FINAL_WORDS = frozenset(
    """a an the to of in on at for by with from into onto about over under after before
    until while and or but so because that which who whose if as than then my your his her
    its our their this these those is are was were be been being will would can could should
    may might must do does did have has had i we you they he she just very really also
    like""".split()
)


def ends_with_non_final_word(text: str) -> bool:
    words = text.split()
    return bool(words) and _norm(words[-1]) in _NON_FINAL_WORDS


def starts_like_sentence(text: str) -> bool:
    """A fragment that opens a new sentence even without a capital letter: a
    direct question ("did you see it", "why don't we") or "let's ..."."""
    stripped = text.strip()
    if re.match(r"(?i)let['’]s\s+(?!say\b)", stripped):
        return True
    if _QUESTION_START_RE.match(stripped) and not re.match(
            r"(?i)(?:what|why|how|where|when|who|whom|whose|which)\s+(?!(?:is|are|was|were|do"
            r"|does|did|can|could|would|will|should|shall|has|have|had|am|don['’]t|doesn['’]t"
            r"|didn['’]t|won['’]t|can['’]t|isn['’]t|aren['’]t)\b)", stripped):
        return True
    return False


_CLAUSE_START_RE = re.compile(
    r"(?i)^(?:i|i['’]m|i['’]ll|i['’]ve|we|we['’]re|we['’]ll|you|you['’]re|they|they['’]re|he"
    r"|she|it['’]s|that['’]s|this\s+is|there['’]s|here['’]s|let['’]s|please|thanks|thank\s+you"
    r"|see\s+you)\b"
)


def starts_clause(text: str) -> bool:
    """Opens a clause of its own: "that's all for now", "let's go", "thanks"."""
    return bool(_CLAUSE_START_RE.match(text.strip()))


def starts_with_name(text: str) -> bool:
    """A capitalized word that isn't a common sentence starter: probably a name
    ("I invited Claude [pause] Gemini ...")."""
    tokens = _tokens(text.strip())
    return bool(tokens) and _is_proper(tokens[0])


# --------------------------------------------------------------------------- facade


@dataclass(frozen=True)
class RuleOptions:
    remove_fillers: bool = True
    voice_commands: bool = True
    words: tuple[str, ...] = ()
    replacements: tuple[tuple[str, str], ...] = ()
    language: str = "en"  # English-only rules (times, "i" -> "I") are skipped otherwise


_DEFAULT_OPTIONS = RuleOptions()


def clean_sentence(text: str, options: RuleOptions | None = None) -> str:
    """Rule-based cleanup for one ASR sentence (or any short text)."""
    options = options or _DEFAULT_OPTIONS
    language = options.language
    if options.remove_fillers:
        text = remove_fillers(text, language)
        text = remove_repetitions(text, language)
    text = apply_dictionary(text, options.words, dict(options.replacements))
    if language == "en":
        text = normalize_times(text)
    text = tidy(text, language)
    if options.voice_commands:
        text = apply_voice_commands(text, language)
    return text


def clean_instruction(text: str) -> str:
    """A spoken speak-to-edit instruction, with fillers and self-corrections
    resolved ("make it shorter, no, make it more formal")."""
    text = clean_sentence(text, RuleOptions(voice_commands=False))
    text = resolve_corrections(text)
    # An unresolved "scratch that" still means: only what follows counts.
    cues = [c for c in find_cues(text) if c.scratch]
    if cues and re.search(r"\w", text[cues[-1].end :]):
        text = upper_first(text[cues[-1].end :].lstrip(" ,.;:!"))
    return text.strip()
