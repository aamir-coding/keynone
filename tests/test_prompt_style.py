"""The prompt style: AI assistants in terminals, editors and AI apps."""

from __future__ import annotations

import pytest

from keynone.cleanup import formatting
from keynone.cleanup.pipeline import CleanupPipeline, CleanupSettings
from keynone.cleanup.spoken import convert
from keynone.config import AppProfile, Config, Style
from keynone.engine.session import compose


def dictate(*fragments: str, style: Style = "prompt") -> str:
    """What the session inserts (before smart spacing) for these recognizer fragments."""
    pipeline = CleanupPipeline(CleanupSettings(llm_mode="off", style=style))
    for fragment in fragments:
        pipeline.add_sentence(fragment)
    text, _ = compose(pipeline.finish(timeout=2).text, AppProfile(style=style), Config())
    return text


@pytest.mark.parametrize(("said", "written"), [
    ("Git status.", "git status"),
    ("Git commit dash m fix the login bug.", "git commit -m fix the login bug"),
    ("npm run dev", "npm run dev"),
    ("Docker compose up dash d.", "docker compose up -d"),
    ("cd source slash keynone", "cd source/keynone"),
    ("Exit.", "exit"),
    ("Camel case user name.", "userName"),
])
def test_commands_and_identifiers_are_written_as_said(said: str, written: str) -> None:
    assert dictate(said) == written


@pytest.mark.parametrize(("said", "written"), [
    ("Can you fix the bug in main dot py and run the tests?",
     "Can you fix the bug in main.py and run the tests?"),
    ("Find the bug in the parser.", "Find the bug in the parser."),
    ("Make sure the tests pass.", "Make sure the tests pass."),
    ("Git is slow today.", "Git is slow today."),
    ("Go ahead and implement it.", "Go ahead and implement it."),
    ("thanks that worked", "Thanks that worked."),
    ("um so first update the docs second run the tests third push",
     "So:\n1. Update the docs\n2. Run the tests\n3. Push"),
])
def test_prompts_are_written_as_prose(said: str, written: str) -> None:
    assert dictate(said) == written


def test_file_mentions() -> None:
    assert dictate("Explain at main dot py and at utils dot ts.") == (
        "Explain @main.py and @utils.ts.")
    # "look at", "is at": English, not a mention
    assert dictate("Look at main dot py, the function at line forty.") == (
        "Look at main.py, the function at line forty.")
    assert dictate("The bug is at app dot tsx.") == "The bug is at app.tsx."
    # recognizers often write "py" as "pi"; after "at" it can only be the file
    assert dictate("Explain at main dot pi.") == "Explain @main.py."
    assert dictate("Print math dot pi.") == "Print math.pi."
    # only the prompt style mentions files
    assert dictate("Explain at main dot py.", style="default") == "Explain at main.py."


@pytest.mark.parametrize(("said", "written"), [
    ("rename camel case user name to camel case full name please",
     "rename userName to fullName please"),
    ("add a snake case get user by id function", "add a get_user_by_id function"),
    ("call it pascal case order service", "call it OrderService"),
    ("the kebab case main nav class", "the main-nav class"),
    ("set constant case max retries to five", "set MAX_RETRIES to five"),
    ("that is all caps urgent", "that is URGENT"),
    ("I prefer camel case for variables", "I prefer camel case for variables"),
])
def test_casing_commands(said: str, written: str) -> None:
    assert convert(said, style="prompt").text == written


@pytest.mark.parametrize(("said", "written"), [
    # The recognizer drops a spoken "dash" but keeps "hyphen" and "minus".
    ("Git commit hyphen M fix the login bug", "Git commit -m fix the login bug"),
    ("Git commit minus M fix the login bug", "Git commit -m fix the login bug"),
    ("ls minus la", "ls -la"),
    ("git log dash dash oneline", "git log --oneline"),
    ("It is a well hyphen known issue", "It is a well-known issue"),  # not a command
    ("five minus three is two", "five minus three is two"),
])
def test_options_in_command_lines(said: str, written: str) -> None:
    assert convert(said, style="prompt").text == written


def test_casing_commands_are_for_code_and_prompts_only() -> None:
    assert convert("we use all caps headings").text == "we use all caps headings"
    assert convert("camel case user name", style="code").text == "userName"


@pytest.mark.parametrize(("text", "command"), [
    ("git status", True), ("npm install", True), ("git push --force", True),
    ("python main.py", True), ("ls", True), ("userName", True), ("@main.py", True),
    ("exit", True), ("git is slow today", False), ("find the bug", False),
    ("make sure it works", False), ("Thanks", False), ("Yes", False),
    ("claude, please fix the tests", False), ("", False),
    ("git commit -m fix the bug", True), ("docker run -p 80:80 nginx", True),
])
def test_looks_like_command(text: str, command: bool) -> None:
    assert formatting.looks_like_command(text) is command


def test_terminals_editors_and_ai_apps_use_the_prompt_style() -> None:
    config = Config()
    assert config.profile_for("WindowsTerminal.exe", "PowerShell").style == "prompt"
    assert config.profile_for("WindowsTerminal.exe", "PowerShell").copy_keys == "ctrl+insert"
    assert config.profile_for("Cursor.exe", "main.py - project").style == "prompt"
    assert config.profile_for("claude.exe", "Claude").style == "prompt"
    assert config.profile_for("chrome.exe", "ChatGPT - Google Chrome").style == "prompt"
    assert config.profile_for("chrome.exe", "Inbox - Gmail").style == "email"
    assert config.profile_for("notepad.exe", "Untitled").style == "default"
    # the language model now helps in terminals too (it was off there)
    assert config.profile_for("WindowsTerminal.exe", "PowerShell").llm_mode is None


def test_code_style_writes_commands_without_a_sentence_capital() -> None:
    assert dictate("Git status.", style="code") == "git status"
    assert dictate("Fix the parser.", style="code") == "Fix the parser."  # as the ASR wrote it
