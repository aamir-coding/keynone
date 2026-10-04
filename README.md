# Keynone

**Local, private voice dictation for Windows — speak naturally, get clean text in any app.**

Hold **Ctrl+Win**, talk, let go: Keynone removes filler words and repetitions,
applies your self-corrections ("…at three, no wait, four PM" → "…at 4 PM"),
formats lists and punctuation, and types the result wherever your cursor is.
Everything runs on your CPU with open models; nothing leaves your computer.

Built and measured on an AMD Ryzen 5 5600H with 16 GB RAM:
live text **≤ 0.81 s** behind your voice, polished text **0.15–0.8 s** after you
release the key, **0 % CPU** while idle.

## Features

| | |
|---|---|
| Works in any app | Paste (clipboard restored, kept out of clipboard history) or Unicode typing per app |
| Removes filler words | um, uh, er, hmm… instantly; "like / you know / I mean" via the local LLM |
| Removes repetition | "I I think", "we need to, we need to talk" |
| Auto-edits when you change your mind | "no / no wait / sorry / I mean / or rather / actually make that", "scratch that" |
| Auto-formats | Numbered/bulleted lists, sentence punctuation and question marks, times ("4:30 PM") |
| Live preview | Overlay with level meter and the latest text; never steals focus |
| Hands-free | Double-tap the hotkey; tap again to finish; Esc cancels |
| Voice commands | "new line", "new paragraph", "scratch that" |
| App-aware | Chat apps (no trailing period), e-mail, terminals/code editors (no LLM, no extra space) |
| Personal dictionary | Your spellings for names and jargon |
| History | Local SQLite; "Copy last dictation" in the tray |
| Private & contained | Offline after setup; every file lives inside this folder |

## Quick start

Requirements: Windows 10/11 x64, [uv](https://docs.astral.sh/uv/), ~3 GB free disk.

```bash
powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1
```

Then double-click **`Keynone.cmd`**. A microphone icon appears in the tray.

* **Hold Ctrl+Win** and speak → release to insert.
* **Double-tap Ctrl+Win** → hands-free; tap again to finish.
* **Esc** → cancel.
* Stop: tray icon → *Quit Keynone*, or `Keynone-stop.cmd`.

The first dictation after Keynone has been idle takes ~5 s before live text
appears (the speech model loads; your audio is already being recorded).

## How it stays out of the way

Only a tiny listener stays running (≈22 MB, 0 % CPU, no microphone, no models).
The engine (≈2 GB) starts when you press the hotkey and unloads after
`lifecycle.keep_warm_seconds` (default 300 s). Set it to `0` for strict
on-demand: nothing heavy stays resident, at the cost of the ~5 s model load on
every dictation.

## Settings

Edit `data/config.toml` (created on first run; tray → *Open settings*).
Changes apply at the next dictation.

```toml
[hotkeys]
dictate = "ctrl+win"        # e.g. "rctrl", "capslock", "ctrl+alt+space", "f9"

[lifecycle]
keep_warm_seconds = 300     # 0 = strict on-demand

[cleanup]
llm_mode = "auto"           # "always" for maximum polish, "off" for rules only

[dictionary]
words = ["Keynone", "PostgreSQL"]
[dictionary.replacements]
"chat gpt" = "ChatGPT"
```

Maximum polish (slower, Apache-2.0 model):
`keynone setup --with qwen3.5-2b-q4km`, then set `llm.model = "qwen3.5-2b-q4km"`
and `cleanup.llm_mode = "always"`.

## Command line

```bash
.venv\Scripts\keynone.exe --help
```

| Command | Purpose |
|---|---|
| `keynone setup [--list] [--with ID] [--remove ID]` | Download/verify models |
| `keynone listen [--dry-run]` | Run the listener (`--dry-run`: show, never type) |
| `keynone stop` | Stop the listener |
| `keynone doctor` | Check installation, models, microphone |
| `keynone devices` | List microphones |
| `keynone history [-n 20]` | Recent dictations |
| `keynone transcribe a.wav [b.wav…] [--realtime]` | Run WAV files through the full pipeline |
| `keynone bench cleanup` / `keynone bench asr` | Quality and speed benchmarks |
| `keynone autostart enable\|disable\|status` | Start at Windows sign-in (creates a Startup shortcut — the only file written outside this folder) |

## Privacy

No network after setup, no telemetry. The local LLM server listens only on
127.0.0.1 with a random key. Transcripts are kept only in `data/history.db`
(disable with `[history] enabled = false`) and never written to logs unless
`privacy.log_transcripts = true`. Password fields are skipped.

## Development

```bash
. .\scripts\env.ps1                     # keeps uv caches/temp inside the folder
uv run pytest                           # unit + integration tests
uv run ruff check src tests && uv run mypy
uv run python scripts/make_test_audio.py   # synthesized test dictations
uv run keynone transcribe --realtime data/test_audio/*.wav
```

Desktop tests (type into their own window, open the mic for ~2 s):
`$env:KEYNONE_DESKTOP_TESTS="1"; uv run pytest tests/test_desktop_*.py`

Docs: [feasibility & model selection](docs/01-feasibility-and-model-selection.md) ·
[specification](docs/02-specification.md) ·
[implementation plan & roadmap](docs/03-implementation-plan.md)

```
src/keynone/
  listener/   hotkey state machine, engine process manager, tray app
  engine/     session, overlay, insertion, history, model loading
  cleanup/    rules, correction resolver, segmenter, pipeline, LLM, guard, prompts
  win32/      ctypes Win32 layer (hook, waveIn, clipboard, SendInput, windows, tray)
  asr.py      streaming speech recognition (sherpa-onnx)
  assets.py   pinned model registry + verified downloader
```

## Models and licenses

| Component | License |
|---|---|
| NVIDIA Nemotron Speech Streaming EN 0.6B (speech) | NVIDIA Open Model License |
| Liquid LFM2.5 1.2B Instruct (cleanup) | LFM Open License v1.0 (free for individuals and organizations under $10M revenue) |
| Qwen3.5 2B (optional) | Apache-2.0 |
| sherpa-onnx / ONNX Runtime | Apache-2.0 / MIT |
| llama.cpp | MIT |

Keynone is an independent project and is not affiliated with Typeless.
