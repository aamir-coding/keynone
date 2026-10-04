"""Deterministic, instant cleanup rules.

Applied to every sentence before (and as the fallback for) LLM polishing:

* filler words (um, uh, er, hmm ...) and stutters / immediately repeated phrases
* spoken commands: "new line", "new paragraph", "scratch that"
* personal-dictionary spellings
* spoken lists ("first ... second ... finally ...", "bullet point ...")
* punctuation / capitalization tidy-up after the edits above

Everything here is conservative: ambiguous fillers such as "like", "you know"
or "I mean" are left for the LLM, which can tell filler from meaning.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

# Marker inserted for a spoken "scratch that"; resolved by the pipeline.
SCRATCH = "␡"  # "SYMBOL FOR DELETE": never produced by ASR

# --------------------------------------------------------------------------- fillers

_FILLER = r"(?:u+h*m+|u+h+|e+r+m+|e+r|a+h+|h+m+|m+h*m+|uh-huh)"
_FILLER_RE = re.compile(
    rf"(?i)(^|[\s,;:.!?…\"'(])(?:{_FILLER})(?:,|…|\.\.\.)?(?=$|[\s,;:.!?…\"')])"
)


def _filler_sub(m: re.Match[str]) -> str:
    word = m.group(0)[len(m.group(1)) :].rstrip(",.…")
    if len(word) > 1 and word.isupper():  # "ER", "AH": acronyms, not fillers
        return m.group(0)
    return m.group(1)


def remove_fillers(text: str) -> str:
    """Remove non-lexical fillers (um, uh, er, hmm, mm ...)."""
    previous = None
    while previous != text:  # adjacent fillers ("um, uh,") need repeated passes
        previous = text
        text = _FILLER_RE.sub(_filler_sub, text)
    return text


# --------------------------------------------------------------------------- repetitions

# Doubled words that are usually intentional.
_KEEP_DOUBLE = frozenset(
    "that had is very really so no bye ha haha knock yeah yes go now well there tut chop "
    "bla blah la hey hi".split()
)
_WORD = r"[^\W\d_](?:[\w'’]*[^\W_])?"  # a word that starts with a letter
_REPEAT_WORD_RE = re.compile(rf"(?i)\b({_WORD})(?:-?[\s,]+\1\b)+")
_REPEAT_NGRAM_RE = re.compile(rf"(?i)\b((?:{_WORD}[\s,]+){{1,3}}{_WORD})[\s,]+\1\b")


def remove_repetitions(text: str) -> str:
    """Remove stutters ("I I think") and restarted phrases ("I want to, I want to go")."""

    def word_sub(m: re.Match[str]) -> str:
        return m.group(0) if m.group(1).lower() in _KEEP_DOUBLE else m.group(1)

    previous = None
    for _ in range(4):
        if previous == text:
            break
        previous = text
        text = _REPEAT_NGRAM_RE.sub(r"\1", text)
        text = _REPEAT_WORD_RE.sub(word_sub, text)
    return text


# --------------------------------------------------------------------------- dictionary


def _phrase_pattern(phrase: str) -> re.Pattern[str] | None:
    words = phrase.split()
    if not words:
        return None
    body = r"[\s-]*".join(re.escape(w) for w in words)
    return re.compile(rf"(?i)(?<![\w]){body}(?![\w])")


def apply_dictionary(
    text: str, words: Iterable[str] = (), replacements: Mapping[str, str] | None = None
) -> str:
    """Apply user replacements, then enforce the casing of dictionary words."""
    for spoken, written in (replacements or {}).items():
        pattern = _phrase_pattern(spoken)
        if pattern:
            text = pattern.sub(lambda _m, w=written: w, text)
    for word in words:
        pattern = _phrase_pattern(word)
        if pattern:
            text = pattern.sub(lambda _m, w=word: w, text)
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


def tidy(text: str) -> str:
    """Fix spacing/punctuation/capitalization left behind by edits."""
    text = text.replace("...", "…")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r" +([,;:.!?…])", r"\1", text)
    text = re.sub(r"[,;:.!?](?:\s*[,;:.!?])+", _collapse_punct, text)
    text = re.sub(r"(^|\n)[\s,;:.]+", r"\1", text)
    text = re.sub(r"([,;])(?=[^\W\d_])", r"\1 ", text)
    text = re.sub(r"(?<![\w.'’-])i(?=(?:['’](?:m|d|ll|ve|s))?(?![\w.'’-]))", "I", text)
    text = _capitalize(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip(" ")


# --------------------------------------------------------------------------- sentences

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?…])\s+(?=\S)|(\n+)")


def split_sentences(text: str) -> list[str]:
    """Split into sentences; runs of newlines are kept as their own items."""
    parts: list[str] = []
    for piece in _SENTENCE_SPLIT_RE.split(text):
        if not piece:
            continue
        if piece.startswith("\n"):
            parts.append(piece)
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


# --------------------------------------------------------------------------- commands

_NEWLINE_CMDS = {"new line", "newline", "next line"}
_PARAGRAPH_CMDS = {"new paragraph", "next paragraph"}
_SCRATCH_CMDS = {
    "scratch that", "delete that", "strike that", "erase that", "scratch that please",
    "delete the last sentence", "remove the last sentence", "delete last sentence",
}
_EDGE_CMD_RE = re.compile(
    r"(?i)^(?P<lead>(?:new|next) (?:line|paragraph))[,.:]?\s+(?P<rest>.+)$"
    r"|^(?P<body>.+?)\s+(?P<trail>(?:new|next) (?:line|paragraph))[.!]?$"
)


def _command_for(sentence: str) -> str | None:
    key = re.sub(r"[^\w\s]", "", sentence).strip().lower()
    key = re.sub(r"\s+", " ", key)
    if key in _NEWLINE_CMDS:
        return "\n"
    if key in _PARAGRAPH_CMDS:
        return "\n\n"
    if key in _SCRATCH_CMDS:
        return SCRATCH
    return None


def _break_for(phrase: str) -> str:
    return "\n\n" if "paragraph" in phrase.lower() else "\n"


def apply_voice_commands(text: str) -> str:
    """Turn standalone spoken commands into line breaks / scratch markers."""
    out: list[str] = []
    for sentence in split_sentences(text):
        if sentence.startswith("\n"):
            out.append(sentence)
            continue
        command = _command_for(sentence)
        if command is not None:
            out.append(command)
            continue
        m = _EDGE_CMD_RE.match(sentence)
        if m and m.group("lead"):
            out.extend([_break_for(m.group("lead")), m.group("rest")])
        elif m and m.group("trail"):
            body = m.group("body").rstrip(";: ")
            if not body.endswith((".", "!", "?", "…", ",")):
                body += "."
            out.extend([body, _break_for(m.group("trail"))])
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


# --------------------------------------------------------------------------- lists

_ORDINALS = {
    "first": 1, "firstly": 1, "second": 2, "secondly": 2, "third": 3, "thirdly": 3,
    "fourth": 4, "fourthly": 4, "fifth": 5, "sixth": 6, "seventh": 7, "eighth": 8,
    "ninth": 9, "tenth": 10,
}
_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10,
}
_MARKER_WORDS = (
    r"(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)(?:ly)?"
    r"|(?:number|step|item)\s+(?:one|two|three|four|five|six|seven|eight|nine|ten|\d+)"
    r"|lastly|finally|last(?:ly)?\s+but\s+not\s+least|and\s+last(?:ly)?|next|then|after\s+that"
)
# A marker opens a clause (start, after punctuation/"and"/"then"), or appears
# mid-sentence immediately followed by a comma/colon ("the steps are first, ...").
_MARKER_RE = re.compile(
    rf"(?i)(?:(?:(?<=^)|(?<=[.;,:!?]\s)|(?<=\band\s)|(?<=\bthen\s))(?:and\s+)?"
    rf"(?P<marker>{_MARKER_WORDS})\b[,:]?\s+"
    rf"|(?<=\s)(?P<marker2>{_MARKER_WORDS})[,:]\s+)"
)


def _marker_text(m: re.Match[str]) -> str:
    return m.group("marker") or m.group("marker2")
_BULLET_RE = re.compile(r"(?i)(?:^|(?<=[\s.,;:]))bullet(?:\s+point)?[,:.]?\s+")


def _marker_rank(marker: str) -> int | None:
    words = marker.lower().split()
    head = words[0].removesuffix("ly") if words[0] not in _ORDINALS else words[0]
    if words[0] in _ORDINALS:
        return _ORDINALS[words[0]]
    if head in _ORDINALS:
        return _ORDINALS[head]
    if words[0] in ("number", "step", "item") and len(words) > 1:
        token = words[1]
        return int(token) if token.isdigit() else _NUMBER_WORDS.get(token)
    return None  # next / then / finally: continuation markers


def _continuation_ok(text: str, m: re.Match[str]) -> bool:
    """next/then/after that only open an item at a sentence boundary;
    finally/lastly may also follow a comma or "and"."""
    before = text[: m.start()].rstrip()
    word = _marker_text(m).lower()
    if word.startswith(("finally", "last", "and last")):
        return before.endswith((".", ";", "!", "?", ",")) or before.lower().endswith(" and")
    return before.endswith((".", ";", "!", "?"))


def _clean_item(item: str) -> str:
    item = item.strip().strip(",;:").strip()
    item = re.sub(r"^(?:and|then|also)\s+", "", item, flags=re.I)
    item = re.sub(r"[.,;:]+$", "", item).strip()
    if item and item[0].islower() and not any(c.isupper() for c in item.split()[0][1:]):
        item = item[0].upper() + item[1:]
    return item


def format_spoken_list(text: str) -> str | None:
    """Turn an enumerated utterance into a numbered or bulleted list.

    Returns ``None`` when the text does not look like a list (fewer than two
    explicit ordinal markers, or no "bullet point" commands).
    """
    bullets = list(_BULLET_RE.finditer(text))
    if len(bullets) >= 2:
        intro = text[: bullets[0].start()].strip()
        items = [
            _clean_item(text[m.end() : (bullets[i + 1].start() if i + 1 < len(bullets) else None)])
            for i, m in enumerate(bullets)
        ]
        return _assemble_list(intro, [f"- {it}" for it in items if it])

    markers = list(_MARKER_RE.finditer(text))
    ranked = [m for m in markers if _marker_rank(_marker_text(m)) is not None]
    if len(ranked) < 2 or _marker_rank(_marker_text(ranked[0])) != 1:
        return None
    start = ranked[0].start()
    items_markers = [
        m
        for m in markers
        if m.start() >= start
        and (_marker_rank(_marker_text(m)) is not None or _continuation_ok(text, m))
    ]
    # Continuation markers (next/then/finally) only count once a list has started.
    intro = text[:start].strip()
    items: list[str] = []
    for i, m in enumerate(items_markers):
        end = items_markers[i + 1].start() if i + 1 < len(items_markers) else len(text)
        items.append(text[m.end() : end])
    outro = ""
    if items:
        last_parts = split_sentences(items[-1].strip())
        if len(last_parts) > 1:
            items[-1] = last_parts[0]
            outro = join_sentences(last_parts[1:])
    cleaned = [_clean_item(it) for it in items]
    numbered = [f"{n}. {it}" for n, it in enumerate((c for c in cleaned if c), start=1)]
    if len(numbered) < 2:
        return None
    return _assemble_list(intro, numbered, outro)


def _assemble_list(intro: str, lines: list[str], outro: str = "") -> str:
    parts = []
    if intro:
        intro = re.sub(r"[\s,.;:]+$", "", intro)
        parts.append(intro + ":")
    parts.extend(lines)
    text = "\n".join(parts)
    if outro:
        text += "\n" + outro.strip()
    return text


# --------------------------------------------------------------------------- cue detection

_CORRECTION_RE = re.compile(
    r"(?i)(?<![\w'])(?:"
    r"no,?\s+wait|wait,?\s+no|no,?\s+no|actually|i\s+mean(?:t)?|sorry|or\s+rather|"
    r"make\s+(?:that|it)|change\s+(?:that|it)\s+to|correction|scratch\s+that|strike\s+that|"
    r"let\s+me\s+rephrase|on\s+second\s+thought|never\s*mind|rather\s+than|instead|"
    r"not\s+\w+(?:\s+\w+)?,?\s+but"
    r")(?![\w'])"
    r"|(?:^|[,.;]\s*)no[,.]\s"  # "at 5, no, 6"
)
_SOFT_FILLER_RE = re.compile(
    r"(?ix)(?:^|[,.!?]\s*)(?:like|you\s+know|i\s+mean|kind\s+of|sort\s+of|basically|"
    r"literally|you\s+see|okay\s+so|so\s+yeah|well)\s*,"
    r"|,\s*(?:like|you\s+know|i\s+mean|basically|literally|right)\s*[,.?]"
)


def has_correction_cue(text: str) -> bool:
    return bool(_CORRECTION_RE.search(text))


def starts_with_correction_cue(text: str) -> bool:
    m = _CORRECTION_RE.search(text)
    return bool(m) and m.start() <= 2


def has_soft_fillers(text: str) -> bool:
    return bool(_SOFT_FILLER_RE.search(text))


def has_list_cue(text: str) -> bool:
    ranked = [m for m in _MARKER_RE.finditer(text) if _marker_rank(_marker_text(m))]
    return len(ranked) >= 2 or len(_BULLET_RE.findall(text)) >= 2


def starts_list_item(text: str) -> bool:
    m = _MARKER_RE.match(text) or _BULLET_RE.match(text)
    return bool(m)


def starts_first_list_item(text: str) -> bool:
    """True for "First, ...", "Number one, ...", "Step 1: ..." and "Bullet point ..."."""
    m = _MARKER_RE.match(text)
    if m:
        return _marker_rank(_marker_text(m)) == 1
    return bool(_BULLET_RE.match(text))


# --------------------------------------------------------------------------- self-corrections
#
# "<A>, no, <B>" / "<A>. Sorry, <B>": the speaker replaces the tail of A with B.
# We only resolve when the replaced span X (the tail of A) can be identified
# confidently; anything ambiguous is left for the LLM (or left as dictated).

_STRONG_CUE = (
    r"(?:no,?\s+wait|wait,?\s+no|no,?\s+no|no|sorry|i\s+mean(?:t)?|or\s+rather|"
    r"correction|scratch\s+that|make\s+(?:that|it))"
)
_CUE_TAIL = r"(?:\s*[,:]?\s+(?:actually,?\s+)?(?:i\s+mean(?:t)?,?\s+)?(?:make\s+(?:that|it)\s+)?)"
_INLINE_CORRECTION_RE = re.compile(rf"(?i),\s*{_STRONG_CUE}{_CUE_TAIL}")
_LEADING_CORRECTION_RE = re.compile(rf"(?i)^(?:actually,?\s+)?{_STRONG_CUE}{_CUE_TAIL}")

_NUMBER_TOKENS = frozenset(
    """zero oh one two three four five six seven eight nine ten eleven twelve thirteen fourteen
    fifteen sixteen seventeen eighteen nineteen twenty thirty forty fifty sixty seventy eighty
    ninety hundred thousand million billion half quarter noon midnight""".split()
)
_DAY_TOKENS = frozenset(
    "monday tuesday wednesday thursday friday saturday sunday today tomorrow tonight".split()
)
_MONTH_TOKENS = frozenset(
    "january february march april may june july august september october november december".split()
)
_FUNCTION_WORDS = frozenset(
    """a an the this that these those my your his her its our their i you he she we they it me
    him us them is are was were be been am do does did have has had will would can could should
    may might must to of in on at for by with from and or but so if as not no yes just also
    very really there here it's i'm we're they're you're i'll we'll i've let's""".split()
)
_JOINERS = frozenset(
    "for to at in on by with from and or please instead tomorrow today tonight".split()
)


def _norm(word: str) -> str:
    return word.lower().strip(".,;:!?\"'()“”‘’")


def _token_type(word: str) -> str | None:
    if any(c.isdigit() for c in word) or word in _NUMBER_TOKENS or word in _ORDINALS:
        return "number"
    if word in _DAY_TOKENS:
        return "day"
    if word in _MONTH_TOKENS:
        return "month"
    return None


def _replace_tail(a: str, b: str) -> str | None:
    """Return A with its corrected tail replaced by B, or None if unsure."""
    a_spans = list(re.finditer(r"\S+", a))
    b_words = [_norm(w) for w in b.split()]
    b_words = [w for w in b_words if w]
    if not a_spans or not b_words:
        return None
    a_words = [_norm(m.group()) for m in a_spans]
    n = len(a_words)
    tail_start = max(0, n - 8)
    b0 = b_words[0]
    b0_type = _token_type(b0)

    def splice(start: int) -> str:
        return (a[: a_spans[start].start()] + b).strip()

    # R1: B restarts with a word found near the end of A ("room 204, no wait, room 205").
    for i in range(n - 1, tail_start - 1, -1):
        if a_words[i] == b0:
            return splice(i)
    # R2: a new value followed by a repeated unit ("24 hours, no, 48 hours").
    if b0_type == "number" and len(b_words) > 1:
        for q in range(n - 1, max(tail_start, 1) - 1, -1):
            if a_words[q] == b_words[1] and _token_type(a_words[q - 1]) == "number":
                start = q - 1
                while start > 0 and _token_type(a_words[start - 1]) == "number":
                    start -= 1
                return splice(start)
    if b0_type is not None:
        # R3a: a lone value replaces the latest value of the same kind, keeping units
        # ("for 20 minutes, no, 30" -> "for 30 minutes").
        if len(b_words) == 1:
            for end in range(n - 1, max(n - 4, -1), -1):
                if _token_type(a_words[end]) == b0_type:
                    start = end
                    while start > 0 and _token_type(a_words[start - 1]) == b0_type:
                        start -= 1
                    head = a[: a_spans[start].start()]
                    rest = a[a_spans[end].end() :]
                    value = b.strip().rstrip(".,;:!?")
                    tail = b.strip()[len(value) :]
                    return (head + value + rest.rstrip(".,;:!?") + tail).strip()
        # R3b: A ends with a value of the same kind ("Monday, or rather Tuesday morning").
        if _token_type(a_words[-1]) == b0_type:
            start = n - 1
            while start > 0 and _token_type(a_words[start - 1]) == b0_type:
                start -= 1
            return splice(start)
    # R4: one content word swapped for another ("grab coffee, no, tea for me").
    last = a_words[-1]
    first_raw = b.split()[0]
    closes = first_raw.endswith((",", ".", "?", "!"))
    if (
        last not in _FUNCTION_WORDS
        and b0 not in _FUNCTION_WORDS
        and (len(b_words) == 1 or closes or b_words[1] in _JOINERS)
    ):
        return splice(n - 1)
    return None


def resolve_corrections(text: str) -> str:
    """Apply confidently-resolvable spoken self-corrections."""
    sentences = split_sentences(text)
    out: list[str] = []
    for sentence in sentences:
        current = sentence
        # Cross-sentence: "... on the 12th. Actually, make it the 14th."
        m = _LEADING_CORRECTION_RE.match(current)
        if m and out and not out[-1].startswith("\n"):
            replaced = _replace_tail(out[-1], current[m.end() :])
            if replaced:
                out[-1] = replaced
                continue
        # In-sentence: "... at 3, no wait, 4 p.m. ..."
        for _ in range(3):
            m = _INLINE_CORRECTION_RE.search(current)
            if not m or m.start() == 0:
                break
            replaced = _replace_tail(current[: m.start()], current[m.end() :])
            if not replaced:
                break
            current = replaced
        out.append(current)
    result = join_sentences(out)
    return _capitalize(result) if result != text else text


# --------------------------------------------------------------------------- facade


@dataclass(frozen=True)
class RuleOptions:
    remove_fillers: bool = True
    voice_commands: bool = True
    words: tuple[str, ...] = ()
    replacements: tuple[tuple[str, str], ...] = ()


def clean_sentence(text: str, options: RuleOptions = RuleOptions()) -> str:
    """Rule-based cleanup for one ASR sentence (or any short text)."""
    if options.remove_fillers:
        text = remove_fillers(text)
        text = remove_repetitions(text)
    text = apply_dictionary(text, options.words, dict(options.replacements))
    text = tidy(text)
    if options.voice_commands:
        text = apply_voice_commands(text)
    return text
