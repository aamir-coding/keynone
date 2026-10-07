"""Final layout: joining polished units, list normalization, per-app touches."""

from __future__ import annotations

import re
from collections.abc import Iterable

from keynone.cleanup.language import detect_language, function_words
from keynone.cleanup.rules import close_before_break, is_question
from keynone.config import Style

_NUMBERED_RE = re.compile(r"^(\s*)(\d+)[.)]\s+(.*)$")
_BULLET_RE = re.compile(r"^(\s*)[-*•–]\s+(.*)$")


def is_list_line(line: str) -> bool:
    return bool(_NUMBERED_RE.match(line) or _BULLET_RE.match(line))


def normalize_lists(text: str) -> str:
    """Renumber numbered blocks from 1 and use ``- `` for bullets."""
    out: list[str] = []
    counter = 0
    for line in text.split("\n"):
        numbered = _NUMBERED_RE.match(line)
        bullet = _BULLET_RE.match(line)
        if numbered:
            counter += 1
            out.append(f"{numbered.group(1)}{counter}. {numbered.group(3)}")
            continue
        counter = 0
        out.append(f"{bullet.group(1)}- {bullet.group(2)}" if bullet else line)
    return "\n".join(out)


# Punctuation that ends a sentence, or a snippet at the end (cleanup/snippets.py).
_SENTENCE_END_RE = re.compile(r"[.!?…:;,。！？；：，।؟-][\"'”’)\]」』]*$")
_CJK_RE = re.compile(r"[぀-ヿ一-鿿]")  # kana + Han (no spaces between)
_FULL_STOP = {"zh": "。", "ja": "。", "hi": "।"}


def _last_sentence(line: str) -> str:
    parts = re.split(r"(?<=[.!?…。！？।])\s*", line.strip())
    parts = [p for p in parts if p]
    return parts[-1] if parts else line


def _close_sentence(text: str) -> str:
    """End an unpunctuated sentence: "?" for English questions, else the full stop
    of its language ("." / "。" / "।")."""
    last_line = text.rstrip().rsplit("\n", 1)[-1]
    if not last_line.strip() or is_list_line(last_line) or _SENTENCE_END_RE.search(last_line):
        return text
    sentence = _last_sentence(last_line)
    language = detect_language(sentence)
    if language == "en":
        mark = "?" if is_question(sentence) else "."
    else:
        mark = _FULL_STOP.get(language, ".")
    return text.rstrip() + mark


def _close_before_break(text: str) -> str:
    """Like :func:`rules.close_before_break`, with full stops of other languages."""
    last_line = text.rstrip().rsplit("\n", 1)[-1]
    if detect_language(last_line) == "en":
        return close_before_break(text)
    return _close_sentence(text)


def _separator(left: str, right: str) -> str:
    """No space between Chinese/Japanese sentences; one space otherwise (incl. Korean)."""
    left_cjk = bool(left) and (_CJK_RE.match(left[-1]) or left.endswith(("。", "！", "？")))
    right_cjk = bool(right) and _CJK_RE.match(right[0])
    return "" if left_cjk and right_cjk else " "


def join_units(units: Iterable[str]) -> str:
    """Join cleaned units; each unit is a separate sentence (or list/paragraph)."""
    out = ""
    for unit in units:
        if not unit:
            continue
        if out and not out.endswith("\n"):
            out = _close_before_break(out) if unit.startswith("\n") else _close_sentence(out)
        if not out or out.endswith("\n") or unit.startswith("\n"):
            out += unit
        elif is_list_line(out.rsplit("\n", 1)[-1]) or is_list_line(unit.split("\n", 1)[0]):
            out += "\n" + unit
        else:
            out += _separator(out, unit) + unit
    return out


def _edge_breaks(text: str) -> tuple[str, str, str]:
    """(leading line breaks, body, trailing line breaks) - at most two each."""
    lead = re.match(r"^\s*", text).group(0).count("\n")  # type: ignore[union-attr]
    trail = re.search(r"\s*$", text).group(0).count("\n")  # type: ignore[union-attr]
    return "\n" * min(lead, 2), text.strip(), "\n" * min(trail, 2)


_OPENERS = "([{\"'“‘«¿¡/"
_CLOSERS = ".,;:!?)]}\"'”’»…"
_SENTENCE_ENDS = ".!?…。！？।"
_KEEP_CASE = frozenset({"i", "i'm", "i'll", "i've", "i'd", "i’m", "i’ll", "i’ve", "i’d"})
_LOWERCASE_STARTERS = function_words("en") | frozenset(
    """also maybe please thanks thank then everyone everybody anyone anybody someone somebody
    everything anything something nothing all both each every let's it's that's there's
    what's here's we're they're you're we'll they'll you'll it'll yes no okay ok sure now today
    tomorrow yesterday tonight because since unless although though however otherwise instead
    still even only never always sometimes usually perhaps probably actually basically just
    one two three which who whose when where why how if as than like some any most more less
    honestly definitely certainly obviously hopefully unfortunately luckily maybe again soon
    later first next finally then here there whatever whenever wherever really very quite
    almost already another other others many much few several plenty""".split()
)


def lowercase_starter(word: str) -> bool:
    """A common word that is written in lowercase mid-sentence (not a name, not "I")."""
    lowered = word.lower()
    return lowered in _LOWERCASE_STARTERS and lowered not in _KEEP_CASE


def _first_word(text: str) -> str:
    match = re.match(r"\s*([^\s]+)", text)
    return match.group(1).strip(",.;:!?\"'()") if match else ""


def _lower_first_if_safe(text: str) -> str:
    word = _first_word(text)
    if (not word or word.lower() in _KEEP_CASE or any(c.isupper() for c in word[1:])
            or word.lower() not in _LOWERCASE_STARTERS):
        return text  # names, "I", acronyms, iPhone-style words keep their case
    index = text.find(word[0])
    return text[:index] + text[index].lower() + text[index + 1 :]


def _upper_first(text: str) -> str:
    for index, char in enumerate(text):
        if char.isalpha():
            return text[:index] + char.upper() + text[index + 1 :]
        if not char.isspace() and char not in _OPENERS:
            break
    return text


def _opens(before: str) -> bool:
    """The text before the caret ends with an opening bracket/quote (no space after it).
    A straight quote opens only after whitespace or another opener: 'said "hi"|' closes."""
    if not before or before[-1] not in _OPENERS:
        return False
    if before[-1] in "\"'":
        return len(before) == 1 or before[-2].isspace() or before[-2] in _OPENERS
    return True


def adapt_to_context(text: str, before: str, after: str, *, style: Style,
                     trailing_space: bool) -> str:
    """Fit inserted text to the characters around the cursor (smart spacing).

    ``before``/``after`` are the texts just before/after the caret, read via UI
    Automation. Handles: casing mid-sentence vs. at a sentence start, a space
    between words, lists starting on their own line, spoken line breaks that the
    text before the cursor already provides, no doubled punctuation, and dropping
    the period when the sentence continues after the cursor.
    """
    lead_breaks, body, trail_breaks = _edge_breaks(text)
    if not body:
        if not lead_breaks:
            return text
        # Only "new line"/"new paragraph" was said: don't pile up empty lines.
        have = len(before) - len(before.rstrip("\n")) if before.strip() else 2
        return "\n" * max(0, len(lead_breaks) - have)
    if lead_breaks:
        have = len(before) - len(before.rstrip("\n")) if before.strip() else 2
        lead_breaks = "\n" * max(0, len(lead_breaks) - have)
        if lead_breaks:
            before = before + lead_breaks  # the text now starts on a fresh line
    line_before = before.rsplit("\n", 1)[-1]
    at_line_start = not line_before.strip()
    # Last meaningful character, looking through opening brackets/quotes: "(" after
    # "a jacket" is mid-sentence, while "(" after "Done." starts a sentence.
    last = line_before.rstrip(" \t" + _OPENERS)[-1:]
    if not last and line_before.strip():
        last = "."  # only brackets/quotes on the line: treat as a sentence start
    first_line = body.split("\n", 1)[0]
    multi_line = "\n" in body
    starts_list = is_list_line(first_line)
    cjk_join = bool(_CJK_RE.match(body[0])) and (not before or bool(_CJK_RE.match(before[-1]))
                                                  or before[-1] in "。！？，、")
    if starts_list:
        leading = "" if at_line_start else "\n"  # a list starts on its own line
    elif (at_line_start or before[-1].isspace() or _opens(before) or cjk_join
          or body[0] in ",.;:!?)]}…"):
        leading = ""
    else:
        leading = " "
    if not starts_list and style != "code":
        if at_line_start or last in _SENTENCE_ENDS:
            body = _upper_first(body)
        elif last.isalnum() or last in ",;:-–—":
            body = _lower_first_if_safe(body)
    following = after[:1]
    next_visible = after.lstrip(" \t")[:1]
    if trail_breaks:
        trailing = ""
    elif multi_line or following in ("", "\n"):
        trailing = " " if (trailing_space and not is_list_line(body.rsplit("\n", 1)[-1])
                           and not _CJK_RE.match(body[-1])
                           and body[-1] not in "。！？([{") else ""
    elif following.isspace():
        trailing = ""
    elif following in _CLOSERS:
        body = body.rstrip(".!?…")  # the existing punctuation follows
        trailing = ""
    else:
        trailing = "" if _CJK_RE.match(following) else " "
    single_sentence = not re.search(r"[.!?…]\s", body.rstrip(".!?…"))
    if (not multi_line and not trail_breaks and single_sentence and next_visible
            and (next_visible.islower() or next_visible.isdigit())):
        body = body.rstrip(".")  # the sentence continues after the cursor
    return lead_breaks + leading + body + trail_breaks + trailing


# Programs that start a command line. Only names that are not everyday words: "find the
# bug in the parser" and "make sure the tests pass" are prompts, not commands.
_CLI_COMMANDS = frozenset(
    """git gh npm npx pnpm pnpx yarn corepack uv uvx pip pipx python python3 py node deno bun
    nvm fnm pyenv cd ls dir mkdir rmdir rm del cls pwd whoami curl wget docker kubectl helm
    cargo rustc rustup dotnet mvn gradle ssh scp winget choco scoop wsl pwsh powershell cmd
    terraform aws az gcloud ollama keynone ipconfig netstat tasklist taskkill sudo apt brew
    cmake ffmpeg jq tar unzip vim nvim nano emacs conda poetry pytest ruff mypy eslint
    prettier tsc vite flutter adb javac gcc clang chmod chown grep rg sed awk systemctl
    journalctl claude codex gemini aider opencode""".split()
)
_SINGLE_WORD_COMMANDS = frozenset("exit clear".split())  # only when said on their own
# Words that make a line starting with a program name prose: "git is slow today".
_PROSE_WORDS = frozenset(
    """the a an to please can could would should you it is are was were we i that this and
    but why what how which when there their they my your our""".split()
)
_CODE_TOKEN_RE = re.compile(
    r"(?:^|\s)(?:--?[A-Za-z][\w-]*|\.{1,2}[/\\]\S*|~[/\\]\S*|[/\\]\S+|\S+=\S*|\||&&|\$\w+)"
    r"(?=\s|$)")
_IDENTIFIER_RE = re.compile(r"[@#]?[A-Za-z_][\w.$/\\-]*")


def is_program(word: str) -> bool:
    """A program name that starts a command line ("git", "npm", "docker" ...)."""
    return word.lower() in _CLI_COMMANDS


def looks_like_command(text: str) -> bool:
    """A command line or a lone identifier rather than a sentence: "git status", "npm run
    dev", "cd src", "userName", "@main.py" - but not "git is slow today"."""
    stripped = text.strip().rstrip(".")
    words = stripped.split()
    if not words or "\n" in stripped:
        return False
    first = words[0].lower()
    if len(words) == 1:
        token = words[0]
        return bool(_IDENTIFIER_RE.fullmatch(token)) and (
            first in _CLI_COMMANDS or first in _SINGLE_WORD_COMMANDS
            or any(c in token for c in "._@/\\$")
            or (token[:1].islower() and any(c.isupper() for c in token[1:])))  # camelCase
    if first not in _CLI_COMMANDS:
        return False
    if _CODE_TOKEN_RE.search(stripped):
        return True
    return len(words) <= 3 or not any(w.lower().strip(",.?!") in _PROSE_WORDS
                                      for w in words[1:])


def effective_style(style: Style, text: str) -> Style:
    """The style one dictation is written in. The prompt style writes a sentence as prose
    ("default") and a command or identifier as it was said ("code")."""
    if style == "prompt":
        return "code" if looks_like_command(text) else "default"
    return style


def as_command(text: str) -> str:
    """A command as typed: no sentence capital on the program, no closing period."""
    text = text.strip()
    if text.endswith(".") and not text.endswith(".."):
        text = text[:-1]
    first = text.split(" ", 1)[0]
    lowered = first.lower()
    if (lowered in _CLI_COMMANDS or lowered in _SINGLE_WORD_COMMANDS) and first[1:].islower():
        text = lowered + text[len(first):]
    return text


def _short_line_list(text: str) -> bool:
    """The last line is a short label in a multi-line text ("Milk\\nEggs",
    "Best regards\\nAamir"): no period."""
    lines = text.split("\n")
    if len(lines) < 2 or len(lines[-1].split()) > 2:
        return False
    previous = lines[-2].rstrip()
    return bool(previous) and not previous.endswith((".", "!", "?", "…"))


def finalize(
    text: str, *, style: Style, drop_final_period: bool, trailing_space: bool
) -> str:
    """Last touches before insertion into the target app.

    Line breaks spoken at the very start or end ("new paragraph ...") are kept:
    they are where the user wants the text relative to what is already there.
    """
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    lead, text, trail = _edge_breaks(text)
    if not text:
        return lead or trail
    if not any(c.isalnum() for c in text):  # only a spoken mark: "question mark", "comma"
        space = trailing_space and not trail and text[-1] not in "([{"
        return lead + text + trail + (" " if space else "")
    text = normalize_lists(text)
    if style != "code":
        if trail:
            text = _close_before_break(text)
        elif not _short_line_list(text):
            text = _close_sentence(text)
    if (
        style == "chat"
        and drop_final_period
        and not (lead or trail)
        and "\n" not in text
        and text.endswith(".")
        and not text.endswith("..")
        and not re.search(r"[.!?…]\s", text[:-1])
    ):
        text = text[:-1]
    text = lead + text + trail
    if trailing_space and not text.endswith("\n"):
        text += " "
    return text
