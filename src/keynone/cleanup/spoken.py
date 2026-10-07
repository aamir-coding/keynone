"""Spoken punctuation and spoken addresses.

The speech model writes what it hears: "Dear John comma I hope you're well
period", "jane dot doe at example dot com". Before the rest of the cleanup
runs, such words become the marks and addresses they stand for::

    Dear John comma I hope you're well period  ->  Dear John, I hope you're well.
    jane dot doe at example dot com            ->  jane.doe@example.com
    open main dot py                           ->  open main.py

Punctuation words are ordinary words too ("the Oxford comma", "over a period of
time", "colon cancer"), so a word only becomes its mark where it can't be part
of the sentence: not after a determiner ("a comma"), not where the words around
it make it a noun ("period of", "comma splice"), never in the plural. Saying
"literal" first always types the word ("literal comma"). When in doubt the word
stays: a wrong mark changes what was said, a leftover word is easy to spot.
English only; the pipeline leaves other languages alone.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Literal

from keynone.cleanup.formatting import lowercase_starter

# How a mark sits between words: "left" sticks to the word before it ("word,"),
# "right" to the word after it ("(word"), "join" to both ("and/or"), "spaced"
# to neither ("A & B").
Attach = Literal["left", "right", "join", "spaced"]
Check = Literal["plain", "dash", "between", "quote"]


@dataclass(frozen=True)
class _Mark:
    words: tuple[str, ...]
    symbol: str
    attach: Attach
    check: Check = "plain"


def _marks(spoken: str, symbol: str, attach: Attach, check: Check = "plain") -> list[_Mark]:
    return [_Mark(tuple(s.split()), symbol, attach, check) for s in spoken.split("|")]


_MARKS: tuple[_Mark, ...] = tuple(sorted(
    [
        *_marks("comma", ",", "left"),
        *_marks("period|full stop|fullstop", ".", "left"),
        *_marks("question mark", "?", "left"),
        *_marks("exclamation mark|exclamation point", "!", "left"),
        *_marks("colon", ":", "left"),
        *_marks("semicolon|semi colon", ";", "left"),
        *_marks("ellipsis|dot dot dot", "…", "left"),
        *_marks("open bracket|open paren|open parenthesis|left bracket|left paren"
                "|left parenthesis|open round bracket", "(", "right"),
        *_marks("close bracket|closed bracket|close paren|closed paren|close parenthesis"
                "|closed parenthesis|right bracket|right paren|right parenthesis"
                "|close round bracket", ")", "left"),
        *_marks("open square bracket|left square bracket", "[", "right"),
        *_marks("close square bracket|closed square bracket|right square bracket", "]", "left"),
        *_marks("open curly bracket|open curly brace|open brace|left curly bracket"
                "|left brace", "{", "right"),
        *_marks("close curly bracket|closed curly bracket|close curly brace|close brace"
                "|right curly bracket|right brace", "}", "left"),
        *_marks("open quote|open quotes|begin quote|start quote|open quotation mark"
                "|open quotation marks", '"', "right"),
        *_marks("close quote|close quotes|closed quote|closed quotes|end quote|end quotes"
                "|end of quote|unquote|close quotation mark|close quotation marks", '"', "left"),
        *_marks("quote", '"', "right", "quote"),
        *_marks("hyphen", "-", "join"),
        *_marks("dash", "–", "spaced", "dash"),
        *_marks("em dash", "—", "join"),
        *_marks("en dash", "–", "join"),
        *_marks("forward slash", "/", "join"),
        *_marks("slash", "/", "join", "between"),
        *_marks("backslash|back slash", "\\", "join"),
        *_marks("underscore", "_", "join", "between"),
        *_marks("ampersand", "&", "spaced"),
        *_marks("at sign", "@", "right"),
        *_marks("dollar sign", "$", "right"),
    ],
    key=lambda mark: -len(mark.words),
))
_FIRST_WORDS = frozenset(mark.words[0] for mark in _MARKS)
_CLOSING_QUOTES = frozenset(m.words for m in _MARKS if m.symbol == '"' and m.attach == "left")
_CLOSERS = ")]}\""  # marks that end something: any word may follow them

# Before a punctuation word, these make it a noun: "a comma", "the colon", "my period".
_DETERMINERS = frozenset(
    """a an the this that these those my your his her its our their each every any some no
    one another extra missing single double same final last first next whole entire stray
    trailing leading such which what whose""".split()
)
# Words that name the word rather than dictate it: "type comma", "without comma", "the
# word comma" ("use comma separated values" is decided by the word after it).
_MENTION_VERBS = frozenset(
    """use uses used using add adds added adding put puts putting insert inserts inserted
    type typed typing remove removes removed delete deletes deleted without called named word
    words term""".split()
)
# After a punctuation word, these make it a noun or the subject of a sentence.
_NOUN_FOLLOWERS = frozenset(
    """is was are were be been isn't wasn't has had have does did doesn't didn't goes went
    comes came means meant should would will can could must might may of key keys sign
    symbol symbols character characters button rule rules usage errors error placement""".split()
)
_SPECIFIC_MODIFIERS: dict[str, frozenset[str]] = {
    ",": frozenset("oxford serial decimal".split()),
    ".": frozenset(
        """trial grace waiting time notice cooling probation probationary billing payment
        reporting holding rest study honeymoon transition transitional warranty free test
        testing review return refund interim school class menstrual given short long extended
        initial specified certain limited set day week month year hour minute second
        historical historic victorian classical medieval""".split()),
    ":": frozenset(
        """sigmoid transverse ascending descending spastic healthy inflamed lower upper
        irritable""".split()),
    "–": frozenset("mad".split()),
    "-": frozenset("soft hard optional non-breaking nonbreaking".split()),
}
_SPECIFIC_FOLLOWERS: dict[str, frozenset[str]] = {
    ",": frozenset("splice splices separated delimited separator separators".split()),
    ".": frozenset(
        """ends end ended starts start started begins began lasts lasted expires expired runs
        ran pain pains cramps cramp drama dramas piece pieces costume costumes product
        products tracker poverty underwear cup app ending starting during""".split()),
    ":": frozenset(
        """cancer cleanse cleansing surgery health polyp polyps screening exam examination wall
        lining tissue disease problems issues removal biopsy function inflammation""".split()),
    "?": frozenset("over hangs hanging remains remained icon emoji".split()),
    "!": frozenset("icon emoji".split()),
    "–": frozenset(
        """of off out away through into across back cam cams board boards home over around
        to""".split()),
}
# Words after a bare "quote" that make it the noun or verb: "a quote from", "quote me".
_QUOTE_FOLLOWERS = frozenset(
    """from for of me us him her them it this that you they price prices request requests
    page pages marks""".split()
)
# "dash" right after these is the verb: "I had to dash", "we'll dash off".
_VERB_BEFORE = frozenset(
    """to will would can could should must might may gotta i we you they he she let's just
    quickly then i'll we'll you'll they'll i'd we'd""".split()
)
# Words that can't be one side of "Figma/Miro" (a "slash" or "underscore" between them).
_NOT_BETWEEN = frozenset(
    """a an the this that these those my your his her its our their to i we you they he she it
    me us him them will would can could should must might may is are was were be been and or
    but so of in on at for by with from please""".split()
)

# --------------------------------------------------------------------------- tokens

_TOKEN_RE = re.compile(
    r"^(?P<lead>[\"'“‘(\[{¿¡]*)(?P<core>.*?)(?P<trail>[,.;:!?…\"'”’)\]}]*)$", re.S)


@dataclass(frozen=True)
class _Token:
    lead: str
    core: str
    trail: str

    @property
    def word(self) -> str:
        return self.core.lower().replace("’", "'")

    @property
    def text(self) -> str:
        return f"{self.lead}{self.core}{self.trail}"

    @property
    def bare(self) -> bool:
        return not self.lead and not self.trail


def _tokenize(text: str) -> list[_Token]:
    tokens = []
    for raw in text.split():
        match = _TOKEN_RE.match(raw)
        assert match is not None  # the pattern matches any string
        tokens.append(_Token(match["lead"], match["core"], match["trail"]))
    return tokens


# --------------------------------------------------------------------------- addresses

_TLDS = frozenset(
    """com org net edu gov io co ai app dev me tv info biz xyz tech online site store blog
    cloud us uk in de fr es it nl ca au jp cn ru br eu ch se no dk fi pl pt ie nz sg ae za
    mx kr""".split()
)
_EXTENSIONS = frozenset(
    """py pyw js mjs cjs ts tsx jsx json jsonl md txt html htm css scss sass less toml yaml
    yml csv tsv pdf doc docx xls xlsx ppt pptx png jpg jpeg gif svg webp ico mp3 mp4 wav
    exe msi dll bat cmd ps1 psm1 sh bash zsh c cc cpp cxx h hpp cs java kt kts go rs rb php
    sql xml ini cfg conf log zip tar gz tgz lock env ipynb vue svelte dart swift lua pl r
    scala gradle dockerfile tf hcl proto graphql gql rst tex bib""".split()
)
_JOINERS = {"dot": ".", "underscore": "_", "dash": "-", "hyphen": "-"}
# A single word before "at" or "dot" that is no name, so no address: "email me at work
# dot com" is not "me@work.com", and "a dot com company" stays as it is.
_NOT_LABEL = _NOT_BETWEEN | frozenset(_JOINERS) | frozenset(
    """here there home work school office it's that's what who where when how why which
    then now just also very really email mail address site website page file folder
    slash""".split()
)
_LABEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_\-.]*")
_DOMAIN_RE = re.compile(r"(?i)[a-z0-9][a-z0-9\-]*(?:\.[a-z0-9\-]+)+")


def _is_label(token: _Token) -> bool:
    return bool(_LABEL_RE.fullmatch(token.core)) and token.word not in _JOINERS


def _at_words(tokens: list[_Token], i: int) -> int:
    """How many tokens from ``i`` say "@": "at", "at sign", "at the rate (of)"."""
    words = [t.word for t in tokens[i:i + 4]]
    if not words or words[0] != "at" or tokens[i].trail:
        return 0
    if words[1:2] == ["sign"] and tokens[i + 1].bare:
        return 2
    if words[1:3] == ["the", "rate"] and all(t.bare for t in tokens[i + 1:i + 3]):
        return 4 if words[3:4] == ["of"] and tokens[i + 3].bare else 3
    return 1


def _domain(tokens: list[_Token], i: int) -> tuple[str, int] | None:
    """A domain spoken from ``i`` ("example dot co dot uk", or "example.com" heard as one
    token): (the domain, tokens used)."""
    if i >= len(tokens) or tokens[i].lead:
        return None
    first = tokens[i]
    if _DOMAIN_RE.fullmatch(first.core) and first.core.rsplit(".", 1)[-1].lower() in _TLDS:
        return first.core.lower(), 1
    if not _is_label(first) or first.trail or first.word in _NOT_LABEL:
        return None
    labels = [first.core]
    j = i + 1
    while j + 1 < len(tokens) and tokens[j].word in ("dot", "dash", "hyphen") and tokens[j].bare:
        nxt = tokens[j + 1]
        if nxt.lead or not _is_label(nxt):
            break
        labels += [_JOINERS[tokens[j].word], nxt.core]
        j += 2
        if nxt.trail:
            break
    # It ends with a top-level domain after a dot: "example dot com".
    while len(labels) > 1 and not (labels[-2] == "." and labels[-1].lower() in _TLDS):
        labels, j = labels[:-2], j - 2
    if len(labels) < 3:
        return None
    return "".join(labels).lower(), j - i


def _local_part(out: list[_Token]) -> tuple[str, int] | None:
    """The name before "at" at the end of ``out``: (local part, tokens used)."""
    if not out or out[-1].trail or not _is_label(out[-1]):
        return None
    parts = [out[-1].core]
    used = 1
    while used + 2 <= len(out):
        sep, label = out[-1 - used], out[-2 - used]
        if sep.word not in _JOINERS or not sep.bare or label.trail or not _is_label(label):
            break
        parts[:0] = [label.core, _JOINERS[sep.word]]
        used += 2
        if label.lead:
            break
    if used == 1 and (out[-1].word in _NOT_LABEL or out[-1].lead):
        return None
    return "".join(parts).lower(), used


def _path(tokens: list[_Token], j: int, trail: str) -> tuple[str, int, str]:
    """ "slash pricing slash team" after a domain: ("/pricing/team", next index, trail)."""
    path = ""
    while (not trail and j + 1 < len(tokens) and tokens[j].word == "slash"
           and tokens[j].bare and not tokens[j + 1].lead and _is_label(tokens[j + 1])):
        path += "/" + tokens[j + 1].core.lower()
        trail = tokens[j + 1].trail
        j += 2
    return path, j, trail


def _scheme(out: list[_Token]) -> str:
    """ "https colon slash slash" just before a domain (taken out of ``out``)."""
    words = [t.word for t in out[-4:]]
    if len(words) == 4 and words[0] in ("http", "https") and words[1:] == ["colon", "slash",
                                                                          "slash"]:
        del out[-4:]
        return f"{words[0]}://"
    return ""


def _file_name(tokens: list[_Token], i: int, style: str) -> tuple[str, int] | None:
    """ "main dot py", "app dot config dot json", "my underscore notes dot md"; in the
    code and prompt styles also "user dot name" (a member)."""
    first = tokens[i]
    if first.lead or first.trail or not _is_label(first) or first.word in _NOT_LABEL:
        return None
    parts = [first.core]
    j = i + 1
    last_dot = 0
    while j + 1 < len(tokens) and tokens[j].word in _JOINERS and tokens[j].bare:
        nxt = tokens[j + 1]
        if nxt.lead or not _is_label(nxt) or nxt.word in _NOT_BETWEEN:
            break
        parts += [_JOINERS[tokens[j].word], nxt.core]
        if tokens[j].word == "dot":
            last_dot = len(parts)
        j += 2
        if nxt.trail:
            break
    if not last_dot:
        return None
    parts = parts[:last_dot]  # nothing after the last "dot <part>" belongs to it
    used = last_dot
    extension = parts[-1].lower()
    if extension in _EXTENSIONS:
        return "".join(parts[:-1]) + extension, used
    if style in ("code", "prompt") and len(parts) == 3:
        return "".join(parts), used
    return None


def _addresses(tokens: list[_Token], style: str) -> list[_Token]:
    """Spoken e-mail and web addresses and file names, each merged into one token."""
    out: list[_Token] = []
    i = 0
    while i < len(tokens):
        at = _at_words(tokens, i)
        if at and out:
            domain = _domain(tokens, i + at)
            local = _local_part(out) if domain else None
            if domain is not None and local is not None:
                name, used = local
                lead = out[-used].lead
                del out[-used:]
                end = i + at + domain[1]
                out.append(_Token(lead, f"{name}@{domain[0]}", tokens[end - 1].trail))
                i = end
                continue
        domain = _domain(tokens, i)
        if domain is not None and domain[1] > 1:
            end = i + domain[1]
            path, end, trail = _path(tokens, end, tokens[end - 1].trail)
            scheme = _scheme(out)
            out.append(_Token("" if scheme else tokens[i].lead, f"{scheme}{domain[0]}{path}",
                              trail))
            i = end
            continue
        named = _file_name(tokens, i, style)
        if named is not None:
            text, used = named
            out.append(_Token("", text, tokens[i + used - 1].trail))
            i += used
            continue
        out.append(tokens[i])
        i += 1
    return out


# --------------------------------------------------------------------------- code words

# Code and prompt styles: "camel case user name" -> userName.
_CASINGS: dict[tuple[str, ...], str] = {
    ("camel", "case"): "camel", ("camelcase",): "camel", ("pascal", "case"): "pascal",
    ("snake", "case"): "snake", ("kebab", "case"): "kebab", ("constant", "case"): "constant",
    ("all", "caps"): "upper",
}
# Words that end a cased name: "camel case first name to camel case full name", "a snake
# case get user by id function".
_CASE_STOP = frozenset(
    """to and or in on with for the a an please then so but from into as is are was were that
    which because if when while it this at function functions method methods variable
    variables class classes field fields property properties parameter parameters argument
    arguments component components module modules hook hooks constant constants type types
    interface enum struct table tables column columns endpoint route file files folder
    directory test tests""".split()
)
# "look at main.py" is English; "explain at main.py" mentions the file (@main.py).
_AT_VERBS = frozenset(
    """look looks looking looked see sees seeing saw check checks checking checked glance stare
    point points pointing aim arrive arrived located defined declared found is are was were be
    been starts start started ends end ended stop stops begins lives sits line column
    character open opened opens stored saved what what's whats there here""".split()
)
_FILE_TOKEN_RE = re.compile(r"[\w./\\-]+\.([A-Za-z0-9]+)")


def _cased(words: list[str], how: str) -> str:
    lowered = [w.lower() for w in words]
    if how == "camel":
        return lowered[0] + "".join(w.capitalize() for w in lowered[1:])
    if how == "pascal":
        return "".join(w.capitalize() for w in lowered)
    if how == "snake":
        return "_".join(lowered)
    if how == "kebab":
        return "-".join(lowered)
    return "_".join(w.upper() for w in lowered)  # constant case, and "all caps" (one word)


def _casing(tokens: list[_Token]) -> list[_Token]:
    """ "snake case get user by id" -> get_user_by_id (up to four words; "all caps" one)."""
    out: list[_Token] = []
    i = 0
    while i < len(tokens):
        how, n = "", 0
        for words, name in _CASINGS.items():
            span = tokens[i:i + len(words)]
            if tuple(t.word for t in span) == words and all(t.bare for t in span):
                how, n = name, len(words)
                break
        j = i + n
        words_found: list[str] = []
        while how and j < len(tokens) and len(words_found) < (1 if how == "upper" else 4):
            token = tokens[j]
            if token.lead or not re.fullmatch(r"[A-Za-z0-9]+", token.core) \
                    or token.word in _CASE_STOP:
                break
            words_found.append(token.core)
            j += 1
            if token.trail:
                break
        if not words_found:
            out.append(tokens[i])
            i += 1
            continue
        out.append(_Token(tokens[i].lead, _cased(words_found, how), tokens[j - 1].trail))
        i = j
    return out


def _mentions(tokens: list[_Token]) -> list[_Token]:
    """Prompt style: "explain at main dot py" -> "explain @main.py" (how Claude Code,
    Cursor and others refer to files), but "look at main.py" stays English."""
    out: list[_Token] = []
    for token in tokens:
        file = _FILE_TOKEN_RE.fullmatch(token.core)
        if (file and file.group(1).lower() in _EXTENSIONS and not token.lead and out
                and out[-1].word == "at" and out[-1].bare
                and (len(out) < 2 or out[-2].word not in _AT_VERBS)):
            at = out.pop()
            out.append(_Token(at.lead, f"@{token.core}", token.trail))
            continue
        out.append(token)
    return out


# --------------------------------------------------------------------------- marks


@dataclass
class _Out:
    text: str
    mark: bool = False
    attach: Attach = "spaced"


@dataclass
class Converted:
    """A fragment after :func:`convert`."""

    text: str
    marks: Counter[str] = field(default_factory=Counter)  # spoken marks, by symbol
    # Marks at the very start that close what was said before the pause
    # ("... well [pause] period"); they are part of ``text`` too.
    lead: str = ""
    ends_with_mark: bool = False  # e.g. a spoken "period" at the end


def _match(tokens: list[_Token], i: int) -> _Mark | None:
    if tokens[i].word not in _FIRST_WORDS or tokens[i].lead:
        return None
    for mark in _MARKS:
        n = len(mark.words)
        span = tokens[i:i + n]
        if len(span) < n or tuple(t.word for t in span) != mark.words:
            continue
        if any(t.lead for t in span) or any(t.trail for t in span[:-1]):
            continue
        return mark
    return None


def _previous(tokens: list[_Token], i: int, before: str) -> tuple[str, bool]:
    """(the word before ``i``, whether punctuation separates it from ``i``)."""
    if i > 0:
        return tokens[i - 1].word, bool(tokens[i - 1].trail)
    words = re.findall(r"[\w'’-]+", before.lower())
    if not words:
        return "", False
    return words[-1].replace("’", "'"), not before.rstrip()[-1:].isalnum()


def _is_word(tokens: list[_Token], i: int, mark: _Mark, before: str) -> bool:
    """Whether the punctuation word(s) at ``i`` are meant as words, not as the mark."""
    n = len(mark.words)
    prev, separated = _previous(tokens, i, before)
    nxt = tokens[i + n].word if i + n < len(tokens) else ""
    ends_clause = bool(tokens[i + n - 1].trail)  # the recognizer heard a pause after it
    # "item one close bracket": a number before a closing mark is content, not "one comma"
    determiner = prev in _DETERMINERS and not (prev == "one" and mark.symbol in _CLOSERS)
    if not separated and (determiner or prev in _MENTION_VERBS
                          or prev in _SPECIFIC_MODIFIERS.get(mark.symbol, ())):
        return True
    if mark.symbol not in _CLOSERS and mark.attach != "right" and not ends_clause and (
            nxt in _NOUN_FOLLOWERS or nxt in _SPECIFIC_FOLLOWERS.get(mark.symbol, ())):
        return True
    if mark.check == "dash":
        name = tokens[i].core[:1].isupper() and prev != "" and not separated
        return name or (not separated and prev in _VERB_BEFORE)
    if mark.check == "between":
        if (prev, nxt) == ("and", "or"):
            return False
        return (not prev or not nxt or prev in _NOT_BETWEEN or nxt in _NOT_BETWEEN
                or separated or ends_clause)
    if mark.words == ("unquote",) and prev == "quote" and not separated:
        return True  # "quote unquote": an idiom, left alone
    if mark.check == "quote":
        # A bare "quote" opens a quotation only when one is closed later on ("he said
        # quote I'm done end quote").
        if nxt == "unquote" or nxt in _QUOTE_FOLLOWERS:
            return True
        rest = [t.word for t in tokens[i + n:]]
        return not any(tuple(rest[k:k + len(words)]) == words
                       for words in _CLOSING_QUOTES for k in range(len(rest)))
    return False


def _render(items: list[_Out]) -> str:
    text = ""
    for index, item in enumerate(items):
        if index:
            left = items[index - 1]
            glued = (item.mark and item.attach in ("left", "join")) or (
                left.mark and left.attach in ("right", "join"))
            text += "" if glued else " "
        text += item.text
    return text


AUTO_PUNCT = ",.;:!?…"


def _adjust_case(token: _Token, symbol: str) -> _Token:
    """A capital after . ? and !; none after , and ; on words the recognizer only
    capitalized because it thought a sentence ended there."""
    core = token.core
    if not core[:1].isalpha() or any(c.isupper() for c in core[1:]) or re.search(r"[@./]",
                                                                                 core):
        return token
    if symbol in ".?!":
        core = core[0].upper() + core[1:]
    elif symbol in ",;" and core[0].isupper() and lowercase_starter(core):
        core = core[0].lower() + core[1:]
    return _Token(token.lead, core, token.trail)


_TRIGGER_RE = re.compile(r"(?i)\b(?:{}|dot|at)\b".format("|".join(sorted(_FIRST_WORDS))))
_CODE_TRIGGER_RE = re.compile(r"(?i)\b(?:case|camelcase|caps)\b")
_SAYING_RE = re.compile(
    r"(?i)\b(?:said|says|say|asked|asks|wrote|writes|replied|replies|answered|answers|told"
    r"|tells|yelled|shouted|whispered|added|adds|thought|goes)\b"
    r"(?:\s+(?:me|us|him|her|them|you))?[,:]?\s+")


def open_quote(text: str) -> str:
    """The opening quote of a quotation whose spoken "quote" the recognizer didn't hear
    ("he said quote I'm done end quote" often comes out as "He said, I'm done end
    quote"): right after the verb of saying."""
    close = text.rfind('"')
    if close <= 0 or text.count('"') % 2 == 0 or text[close - 1].isspace():
        return text
    saying = list(_SAYING_RE.finditer(text, 0, close))
    if not saying:
        return text
    at = saying[-1].end()
    return f'{text[:at]}"{text[at:]}'


def convert(text: str, *, style: str = "default", before: str = "") -> Converted:
    """Spoken marks and addresses in one recognizer fragment.

    ``before`` is the text that precedes the fragment ("... the [pause] comma is
    missing"). ``style`` is the app's style: in the code and prompt styles "dash" is a
    hyphen that sticks to the next word ("dash dash help" -> "--help").
    """
    code = style in ("code", "prompt")
    if not _TRIGGER_RE.search(text) and not (code and _CODE_TRIGGER_RE.search(text)):
        return Converted(text)
    tokens = _addresses(_tokenize(text), style)
    if code:
        tokens = _casing(tokens)
    if style == "prompt":
        tokens = _mentions(tokens)
    items: list[_Out] = []
    marks: Counter[str] = Counter()
    case_after = ""
    i = 0
    while i < len(tokens):
        mark = _match(tokens, i)
        if mark is not None and items and not items[-1].mark \
                and items[-1].text.lower() == "literal":
            items.pop()  # "literal comma": the word itself, as it was said
            items += [_Out(t.text) for t in tokens[i:i + len(mark.words)]]
            i += len(mark.words)
            case_after = ""
            continue
        if mark is None or _is_word(tokens, i, mark, before):
            token = _adjust_case(tokens[i], case_after) if case_after else tokens[i]
            items.append(_Out(token.text))
            case_after = ""
            i += 1
            continue
        symbol, attach = mark.symbol, mark.attach
        if mark.check == "dash" and style in ("code", "prompt"):
            symbol, attach = "-", "right"  # "git log dash dash oneline"
        if attach == "left" and items and not items[-1].mark:
            # The mark replaces the recognizer's own punctuation before it.
            items[-1].text = items[-1].text.rstrip(AUTO_PUNCT) or items[-1].text
        items.append(_Out(symbol, True, attach))
        marks[symbol] += 1
        case_after = symbol if symbol in ".?!,;" else ""
        i += len(mark.words)
    lead = ""
    for item in items:
        if not (item.mark and item.attach == "left"):
            break
        lead += item.text
    text = _render(items)
    if marks['"'] and not lead:
        opened = open_quote(text)
        if opened != text:
            text, marks['"'] = opened, marks['"'] + 1
    return Converted(text, marks, lead, bool(items) and items[-1].mark)


def missing_mark(marks: Counter[str], before: str, after: str) -> str:
    """A spoken mark that ``before`` (the rules' text) has and ``after`` (the LLM's)
    lost, if any. Commas, periods and question marks are the LLM's to fix."""
    for symbol, count in marks.items():
        if symbol not in ",.?" and after.count(symbol) < min(count, before.count(symbol)):
            return symbol
    return ""


def after_mark(mark: str, text: str) -> str:
    """``text`` following a spoken mark it can't be joined to (the dictation started with
    the mark): "," + "and then we" -> ", and then we"."""
    if not text:
        return mark
    first = re.match(r"[^\W\d_][\w'’-]*", text)
    if first and mark[-1:] in ",;:" and lowercase_starter(first.group(0)) \
            and not any(c.isupper() for c in first.group(0)[1:]):
        text = text[0].lower() + text[1:]
    return f"{mark}{'' if text.startswith(chr(10)) else ' '}{text}"
