# Changelog

Notable changes to Keynone. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [0.2.0] - unreleased

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

### Changed

- Cleanup rules rewritten after the stress test: self-corrections, lists, voice
  commands, pauses and the LLM guard (before/after numbers in
  `docs/01-feasibility-and-model-selection.md` §1.7).
- A running Keynone applies saved settings within a second, and a loaded engine
  restarts only for settings it uses. A new hotkey no longer needs one dictation
  with the old hotkey first.
- `keynone setup` installs the edition of the download; `--list` shows both editions.
- Tray menu: *Settings...* and *Edit config.toml* replace *Open settings*.
- `install.ps1` no longer installs the developer tools (pytest, ruff, mypy).

### Fixed

- A config change detected when a dictation started replaced the hotkey state in
  the middle of that dictation, so its release could be missed.

## [0.1.0]

First working version (not released on its own): hold Ctrl+Win to dictate into
any app, live preview, filler and repetition removal, self-corrections, lists,
voice commands, app profiles, dictionary, history, and a dormant listener that
loads the speech engine only on demand.
