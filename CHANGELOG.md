# Changelog

Notable changes to Keynone. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- **Spoken punctuation:** say "comma", "period", "question mark", "colon",
  "open bracket", "end quote", "hyphen"… to type the mark. Keynone tells the mark
  from the word by the words around it ("the Oxford comma", "a period of time",
  "colon cancer" stay words), and "literal comma" always types the word. A
  dictation that is only "question mark" inserts "?". Settings → Cleanup.
- **Spoken addresses:** "jane dot doe at example dot com" (or "at the rate")
  becomes jane.doe@example.com; web addresses ("example dot com slash pricing")
  and file names ("main dot py") are written the way they look.
- **Prompt style** for AI assistants, used in terminals, code editors and AI
  apps (Claude Code, Codex, Cursor, ChatGPT…): a sentence is written as prose, a
  command as you said it ("git status", "npm run dev"); "camel case user name" →
  userName, "snake case get user by id" → get_user_by_id; "explain at main dot
  py" → "explain @main.py". Choose it for your own profiles too.

- **Snippets:** saved text inserted by voice. Say the phrase on its own ("my
  address"), or "insert" and the phrase while dictating ("you can reach me at
  insert my email"); the text goes in exactly as written, several lines too.
  Settings → Snippets, or `[snippets]` in config.toml.

### Changed

- Terminals no longer turn the local AI off: prompts dictated to Claude Code or
  Codex get the same cleanup as everywhere else. Commands are still typed as said.
- Code editors use the prompt style instead of the code style.

## [0.2.0] - 2026-10-07

### Added

- **Settings window** for every option: tray icon → *Settings...*,
  `Keynone-settings.cmd` or `keynone settings`. It edits `data/config.toml` in
  place (your comments and notes stay), checks values before saving, records
  shortcuts by pressing them, and follows the Windows light/dark mode.
- **Two editions.** *Keynone* (English, ~1.4 GB of models) and *Keynone +
  languages* (with the language pack, ~2.0 GB). `keynone setup --edition` and
  `install.ps1 -Edition` choose what to download; the language pack can be added
  later. Without it, language settings fall back to English instead of failing.
- **Speak-to-edit:** select text, hold Ctrl+Win+Alt, say how to change it.
- **Smart spacing:** reads the text around the cursor (UI Automation) for spacing
  and capitals; password fields are detected in browsers too.
- **Other languages** (language pack): dictation in 40 locales, language-aware
  cleanup, and translation of every dictation.
- **GPU option** for the language model (Vulkan), with automatic CPU fallback.
- **Quiet microphones:** speech too quiet for the recognizer is lifted
  (`audio.normalize_level`).
- A 352-dictation stress test and its harness (`scripts/stress_test.py`).
- MIT license, contributing guide, security policy, CI and release workflows.
- `install.ps1 -Dev` sets up a development copy (adds pytest, ruff and mypy).

### Changed

- Cleanup rules rewritten after the stress test: self-corrections, lists, voice
  commands, pauses and the LLM guard (before/after numbers in
  `docs/01-feasibility-and-model-selection.md` §1.7).
- A running Keynone applies saved settings within a second, and a loaded engine
  restarts only for settings it uses. A new hotkey no longer needs one dictation
  with the old hotkey first.
- `keynone setup` installs the edition of the download; `--list` shows both editions.
- Tray menu: *Settings...* and *Edit config.toml* replace *Open settings*.
- Smaller installs: `install.ps1` no longer installs the developer tools (pytest,
  ruff, mypy), clears its download cache when done, and stops a running Keynone
  before updating. The release downloads leave out the tests and the stress-test
  data and scripts.
- Saved prompt caches of the language model (`data/cache/llm-slots`, 3-25 MB each)
  no longer pile up: ones unused for 14 days are deleted, and at most 8 are kept.

### Fixed

- A config change detected when a dictation started replaced the hotkey state in
  the middle of that dictation, so its release could be missed.

## [0.1.0] - 2026-10-05

First working version (not released on its own): hold Ctrl+Win to dictate into
any app, live preview, filler and repetition removal, self-corrections, lists,
voice commands, app profiles, dictionary, history, and a dormant listener that
loads the speech engine only on demand.

[Unreleased]: https://github.com/aamir-coding/keynone/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/aamir-coding/keynone/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/aamir-coding/keynone/releases/tag/v0.1.0
