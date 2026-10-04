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

## 3.3 Roadmap (towards full Typeless parity)

| Priority | Feature | Approach |
|---|---|---|
| P1 | Context-aware insertion | UI Automation: password detection in browsers, mid-sentence casing/spacing from text before the caret |
| P1 | "Speak to edit" selected text | Second chord: read selection (UIA/clipboard), dictate an instruction, rewrite with Qwen3.5 2B, replace |
| P1 | Faster cold start | Moonshine v2 "bridge" for live text while Nemotron loads, or adaptive warm period |
| P2 | Multilingual + translation | NVIDIA Nemotron 3.5 ASR Streaming 0.6B (40 locales, sherpa-onnx exports available) + LLM translation |
| P2 | GPU acceleration (optional) | llama.cpp Vulkan build for the GTX 1650 / Radeon iGPU; CUDA EP for ASR |
| P2 | Per-app tone | Style hints in the LLM prompt per app profile (email formal, chat casual), with guardrails |
| P3 | Personal style learning | Learn dictionary entries from user edits; LoRA-fine-tune a ≤1B model on synthetic disfluent→clean pairs to replace prompting |
| P3 | Settings UI & installer | Small settings window; signed MSIX/Inno installer; update check |
| P3 | Snippets & stats | Voice shortcuts ("insert my address"), words/WPM/time-saved dashboard |

## 3.4 Testing strategy

| Layer | What | Where |
|---|---|---|
| Unit | Rules, resolver, segmenter, pipeline (fake LLM), guard, formatting, hotkey gestures, config, IPC | `tests/` (always run) |
| Quality eval | 34 held-out dictations through rules + LLM + guard | `keynone bench cleanup` |
| Speed | ASR RTF / worst chunk / load time | `keynone bench asr` |
| Integration | Real engine process driven over IPC (headless, dry run) | `tests/test_engine_ipc.py` (needs models) |
| Desktop (opt-in) | Paste/type into own text box + clipboard restore; mic capture; listener session | `KEYNONE_DESKTOP_TESTS=1` |
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
| Model licenses | Commercial use limits | LFM2.5 (LFM Open License, free < $10M revenue); Apache-2.0 Qwen3.5 alternative; NVIDIA Open Model License for ASR |
