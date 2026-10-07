# 3. Implementation plan, status and roadmap

## 3.1 Delivered (v0.1)

| Phase | Scope | Status |
|---|---|---|
| 0. Spike & benchmarks | Hardware probe; ASR candidates (Nemotron 160/560 ms, Kroko, Moonshine v2); ORT-format load test; LLM candidates (LFM2.5 1.2B, Qwen3.5 0.8B/2B); prompt framing study | ✅ see doc 01 |
| 1. Foundations | Portable layout, verified downloader (`keynone setup`), typed config, logging, IPC protocol | ✅ |
| 2. Speech | sherpa-onnx streaming wrapper, sentence segmenter, live-lag metric | ✅ |
| 3. Cleanup | Rules (fillers, repetition, commands, lists, times, punctuation), self-correction resolver, incremental pipeline, LLM polisher with prompt cache, guardrails, eval set (34 cases) | ✅ |
| 4. Desktop | Low-level hotkey hook + gesture state machine, MME capture, clipboard paste/restore, Unicode typing, overlay, tray, sounds, job objects | ✅ |
| 5. Lifecycle | Dormant listener, on-demand engine, warm period / strict mode, dry-run, stop, autostart | ✅ |
| 6. Quality | 160+ unit tests, engine IPC integration test, opt-in desktop tests, ruff + mypy clean, end-to-end runs on synthesized speech | ✅ |
| 7. Extras (v0.2) | Speak-to-edit, smart spacing + UIA password detection, multilingual speech + language-aware cleanup + translation, optional GPU (Vulkan) with CPU fallback | ✅ see doc 01 §1.6 |
| 8. Stress test & hardening | 352-case adversarial corpus, real-recognizer replay with 5 voices, correction grammar, list and pause handling, alignment guard, quiet-microphone normalizer | ✅ see doc 01 §1.7 |
| 9. Settings window | Tk window for every option (FR-45…FR-50): comment-preserving edits of `config.toml` (tomlkit), validation, shortcut recording, light/dark, About page; listener applies saved settings within a second | ✅ |
| 10. Editions & open source | Keynone / Keynone + languages from one codebase (FR-51…FR-54): `setup --edition`, `install.ps1 -Edition`, safe fallback without the language pack, reproducible release zips; MIT license, CHANGELOG, CONTRIBUTING, SECURITY, CI + release workflows, issue templates | ✅ |
| 11. Toward release (v0.3) | Spoken punctuation and addresses (FR-55, FR-56), prompt style (FR-57), snippets (FR-58), history page and paste-last (FR-59, FR-60), home page with statistics (FR-61), learning from corrections (FR-62), smart warm-up (FR-63), tray toggles and restart-free settings (FR-64, FR-65); one pull request each into `release/0.3` | ✅ see doc 01 §1.8 |

## 3.2 Your 5-minute verification (real voice)

1. Double-click `Keynone.cmd` (a microphone icon appears in the tray).
2. Open Notepad, hold **Ctrl+Win**, say: *"um so let's meet at three no wait four PM tomorrow"*, release.
   Expect: `So let's meet at 4 PM tomorrow.` — the first dictation waits ~5 s for the
   model (the overlay says "Starting up – already recording").
3. Try a list: *"for the trip we need three things first book flights second the hotel and third a car"*.
4. Try *"scratch that"* after a sentence, *"new paragraph"*, a question, and Slack/Teams (no final period).
5. Double-tap **Ctrl+Win** for hands-free; tap once more to finish. **Esc** cancels.
6. `keynone history` shows what was inserted; `data/logs/` has timings.

If your accent or vocabulary trips the recognizer, add names/terms to
`[dictionary]` in `data/config.toml`.

### Extras (v0.2)

7. **Smart spacing:** in Notepad type "I think" (no space), hold Ctrl+Win, say
   *"we should ship it on Friday"* → `I think we should ship it on Friday.`
   Put the cursor between two words and dictate a word: no capital, single spaces.
8. **Speak-to-edit:** select a sentence, hold **Ctrl+Win+Alt**, say *"make it more
   formal"* (or *"shorter"*, *"fix the grammar"*, *"translate to Hindi"*), release.
   Ctrl+Z restores the original.
9. **Multilingual:** `keynone setup --edition languages`, set
   `[asr] language = "auto"` (or `"hi"`), dictate in your language. Optional:
   `[cleanup] translate_to = "English"`.
10. **GPU:** `keynone setup --with llama.cpp-b11398-vulkan-x64`, set
    `[llm] gpu = "auto"`, run `keynone doctor` (it should show the GTX 1650), then
    compare speak-to-edit speed.
11. **Settings window:** tray icon → *Settings...*. On *General*, press *Record*
    next to the dictation hotkey, press `F9`, *Save*. Holding `F9` dictates at once
    (no restart, no dictation with the old keys first). Open `data/config.toml`:
    only that line changed, every comment is still there. Change it back.
12. **Editions:** `uv run python scripts/build_release.py --working-tree` builds
    both zips into `dist/`. Extract `keynone-0.2.0-windows.zip` somewhere else and
    run its `scripts\install.ps1`: it downloads the base models only (~1.4 GB), and
    the settings window's *Microphone & speech* page offers *Add the language pack*.

### Toward release (v0.3)

13. **Spoken punctuation:** in Notepad say *"Dear John comma I hope you are well
    period see you soon exclamation mark"* → `Dear John, I hope you are well. See
    you soon!`; *"add a comma after the name"* keeps the word. *"my email is jane
    dot doe at example dot com"* → `jane.doe@example.com`.
14. **Snippets:** Settings → *Snippets*, add *my address* with two lines, Save. Say
    *"my address"* → the two lines exactly; *"you can reach me at insert my
    address"* puts them mid-sentence.
15. **Paste last:** dictate into Notepad, click into another window, press
    **Alt+Shift+V**: the same text again. The *History* page lists it (Copy,
    Delete); the *Home* page counts the words.
16. **Learning:** dictate a name the recognizer gets wrong, fix it by hand within
    a minute: a tray message says it learned the spelling; the next dictation uses
    it (Settings → *Dictionary* lists it, with Remove).
17. **Prompt style:** in Windows Terminal say *"git status"* → `git status` (no
    capital, no period); *"explain at main dot py"* → `Explain @main.py.`
18. **Tray toggles:** right-click the tray icon, switch *Start and stop sounds*
    off, then dictate: no sounds, and no wait for the models.

## 3.3 Roadmap (towards full Typeless parity)

| Priority | Feature | Approach |
|---|---|---|
| ✅ | Context-aware insertion | Done in v0.2 (UI Automation) |
| ✅ | "Speak to edit" selected text | Done in v0.2 |
| ✅ | Multilingual + translation | Done in v0.2 (Nemotron 3.5 ASR, language-aware cleanup) |
| ✅ | GPU acceleration (optional) | Done in v0.2 for the LLM (Vulkan); ASR stays on CPU (already 3× real time) |
| ✅ | Adaptive warm period | Done in v0.3 (smart warm-up; the cold start itself is unchanged) |
| P1 | Faster cold start | Moonshine v2 "bridge" for live text while Nemotron loads |
| P1 | Multilingual eval set | Held-out dictations per language (like the English 34-case set) to tune the multilingual prompt and cues |
| P2 | Per-app tone | Style hints in the LLM prompt per app profile (email formal, chat casual), with guardrails |
| P2 | "Ask" mode | Dictate a request with nothing selected and get generated text (needs a stronger model or GPU) |
| ✅ | Learning from edits | Done in v0.3 for names and spellings (FR-62) |
| P3 | Personal style learning | LoRA-fine-tune a ≤1B model on synthetic disfluent→clean pairs to replace prompting |
| ✅ | Settings UI | Done in v0.2 (settings window; `config.toml` stays the source of truth) |
| P3 | Installer & signing | Signed MSIX/Inno installer; update check |
| ✅ | Snippets & stats | Done in v0.3 (snippets, home page statistics) |
| P2 | Spoken symbols the recognizer drops | Nemotron leaves out a spoken "dash" and often "quote"; a recognizer fine-tuned on dictated punctuation, or an alternative command word |

## 3.4 Testing strategy

| Layer | What | Where |
|---|---|---|
| Unit | Rules, resolver, segmenter, pipeline (fake LLM), guard, formatting, hotkey gestures, config, IPC | `tests/` (always run) |
| Quality eval | 34 held-out dictations through rules + LLM + guard | `keynone bench cleanup` |
| Speed | ASR RTF / worst chunk / load time | `keynone bench asr` |
| Integration | Real engine process driven over IPC (headless, dry run) | `tests/test_engine_ipc.py` (needs models) |
| Desktop (opt-in) | Paste/type into own text box + clipboard restore; mic capture; listener session; smart spacing; speak-to-edit with the real LLM; learning a corrected name from a real EDIT/RichEdit box; paste-last through the real keyboard hook | `KEYNONE_DESKTOP_TESTS=1` |
| Multilingual | Language detection, per-language rules/cues, strict guard, translation routing; sample recordings via `transcribe --language auto` | `tests/test_language.py`, `tests/test_multilingual.py` |
| Stress (adversarial) | 398 dictations (46 of them spoken punctuation, half where the word must stay) × 4 recognizer styles, rules only and with the LLM; the same cases spoken by 5 voices through the real recognizer; 24 speak-to-edit instructions. The rules-only part runs in every `pytest` | `tests/test_dictation_stress.py`, `scripts/stress_test.py` (doc 01 §1.7, §1.8) |
| v0.3 features | Spoken punctuation and addresses, prompt style and command lines, snippets, history and statistics, learning from corrections, warm-up policy, tray toggles, restart rule and live settings | `tests/test_spoken.py`, `test_prompt_style.py`, `test_snippets.py`, `test_history.py`, `test_home.py`, `test_learning.py`, `test_warm.py`, `test_live_settings.py`, `test_listener_reload.py` |
| Microphone level | Level normalizer: normal and moderately quiet speech untouched, quiet speech lifted, silence and sudden loud speech not pumped | `tests/test_audio_level.py` |
| Settings | Every option in the window; edits keep comments and unknown keys; merge with outside edits; broken files; shortcut rules and recording; the window driven like a user (never shown); the listener applying saved settings only when idle | `tests/test_settings_*.py`, `tests/test_listener_reload.py` |
| Editions & disk | Edition detection and the fallback without the language pack; old prompt caches deleted, the ones in use kept | `tests/test_editions.py`, `tests/test_llm_slot_cache.py` |
| End-to-end | Synthesized speech, real-time playback, live-lag + latency metrics | `scripts/make_test_audio.py` + `keynone transcribe --realtime` |

Rule for changes: every bug found end-to-end gets a regression test
(`tests/test_dictation_regressions.py`).

## 3.5 Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Small LLM answers/executes dictated text | Wrong text inserted | Document-continuation prompt, deterministic resolver first, output guard with fallback |
| Recognizer omits punctuation / splits at pauses | Fragmented text | Units re-join fragments; delayed punctuation re-attached; question detection |
| CPU contention (games, builds) | Live lag grows | 560 ms model (RTF 0.30), LLM time budget with rule fallback |
| Cold start ~5 s | First dictation feels slow | Audio buffered; warm period; roadmap bridge model |
| Hotkey conflicts | Unintended dictation | Exact-chord matching, foreign-key cancel, configurable chord, pause in tray |
| Admin windows (UIPI) | Can't type | Detected → clipboard fallback with message |
| Antivirus flags hook + SendInput | App blocked | Open source, no network, documented behavior; code signing on the roadmap |
| Model licenses | Commercial use limits | LFM2.5 (LFM Open License, free < $10M revenue); Apache-2.0 Qwen3.5 alternative; NVIDIA Open Model License / OpenMDW-1.1 for ASR |
| Small LLM changes non-English meaning | Wrong text in other languages | LLM only on correction/filler cues outside English; strict guard (no invented words, no new questions) |
| Speak-to-edit grabs the wrong text | Unwanted replacement | UIA-confirmed selection first; clipboard copy only outside code editors; guard; single undoable paste |
| GPU driver problems | LLM unavailable | Opt-in; automatic CPU fallback with a tray warning |
