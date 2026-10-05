# 2. Keynone — Software Specification

Version 0.1 · Windows 11 x64 · English

## 2.1 Purpose and scope

Keynone is a local, private voice-dictation utility in the spirit of Typeless:
the user holds a hotkey, speaks naturally, and polished text appears in
whichever application has the text cursor. All speech recognition and text
processing run on the local CPU with open-weight models; no network access is
needed after the one-time model download. Out of scope for v0.1: mobile
platforms, cloud sync, non-English languages, voice-driven editing of selected
text (see roadmap).

## 2.2 Definitions

| Term | Meaning |
|---|---|
| Listener | Small resident process: hotkey hook, microphone, tray icon. Holds no models. |
| Engine | On-demand process: speech recognition, cleanup, overlay, text insertion. |
| Session | One dictation, from hotkey press to inserted text. |
| Unit | A sentence (or group of sentences, e.g. a correction or a list) cleaned as a whole. |
| Warm period | Time the engine stays loaded after a session (`keep_warm_seconds`). |
| Live lag | Delay between a word being spoken and appearing in the overlay. |

## 2.3 Functional requirements

### Activation (FR-1…FR-6)
* **FR-1** Hold the dictation chord (default `Ctrl+Win`) to dictate; releasing it inserts the text.
* **FR-2** Double-tap the chord to start hands-free dictation; one more tap finishes it.
* **FR-3** A single short tap (< `hold_threshold_ms`, default 300 ms) discards the recording.
* **FR-4** Pressing another key within the hold threshold (e.g. `Ctrl+Win+D`) is treated as a different shortcut: the dictation is cancelled silently and the key passes through.
* **FR-5** `Esc` cancels an active dictation and is swallowed; otherwise it passes through.
* **FR-6** The chord is configurable (modifier-only chords, single keys such as `capslock`/`f9`, or combinations). Modifier keys are never swallowed; Win/Alt releases are masked so they don't open Start/menus.

### Capture & recognition (FR-7…FR-10)
* **FR-7** Recording starts within ~30 ms of the key press (MME capture in the listener); audio is buffered until the engine is ready, so nothing is lost on a cold start.
* **FR-8** The engine streams 16 kHz mono audio through a streaming transducer with native punctuation and capitalization.
* **FR-9** Recording continues ~200 ms after release to capture the end of the last word.
* **FR-10** Sessions longer than `max_session_seconds` (default 600) stop automatically; hands-free can auto-stop after configurable silence.

### Live preview (FR-11…FR-13)
* **FR-11** An overlay shows a level meter, status (Starting / Listening / Hands-free / Polishing / Inserted / errors), and the latest two lines of text, already cleaned by the instant rules.
* **FR-12** The overlay never takes keyboard focus, is click-through, topmost, DPI-aware, and appears on the monitor of the active window.
* **FR-13** The overlay can be disabled.

### Cleanup (FR-14…FR-22)
* **FR-14 Fillers:** remove non-lexical fillers (um, uh, er, hmm…) deterministically; remove soft fillers (like, you know, I mean…) with the LLM when present.
* **FR-15 Repetition:** remove stutters ("I I"), restarted phrases ("I want to, I want to go"); keep intentional doubles ("very very", "had had", digits).
* **FR-16 Self-correction:** apply "X, no / no wait / sorry / I mean / or rather / actually make that / scratch that, Y" by replacing the corrected span; across sentences too ("…the 12th. Actually, make it the 14th."). Unresolvable cases go to the LLM.
* **FR-17 Voice commands:** "new line", "new paragraph", "scratch that"/"delete that" (removes the previous sentence); tolerant of one recognition slip per word.
* **FR-18 Lists:** ordinal enumerations ("first… second… finally…", "number one…", "step 1…") become numbered lists with the intro as a heading; "bullet point" becomes `- ` items.
* **FR-19 Formatting:** sentence punctuation for unpunctuated speech (incl. `?` for questions), capitalization, times ("four thirty PM" → "4:30 PM"), dictionary spellings.
* **FR-20 LLM polishing** (`auto` / `always` / `off`): faithful cleanup only — never answers, translates or executes dictated text.
* **FR-21 Guardrails:** LLM output is rejected (rule-based text used instead) if it adds content, drops content, looks like an assistant reply, loses a question, or exceeds the time budget (`llm_timeout_seconds`).
* **FR-22** Cleanup runs incrementally per unit while the user speaks.

### Insertion (FR-23…FR-28)
* **FR-23** Insert into the window focused at release time via clipboard paste (default) or Unicode typing (per app).
* **FR-24** The previous clipboard is restored after pasting unless it changed meanwhile; Keynone's temporary clipboard content is excluded from clipboard history and cloud sync.
* **FR-25** Never insert into password fields (Win32 detection); never type while the user still holds modifier keys.
* **FR-26** If insertion isn't possible (no text field, admin window, keys held), copy to the clipboard and say so in the overlay.
* **FR-27 App profiles:** per-process/title style (`default`/`chat`/`email`/`code`), paste keys, trailing space, LLM mode, dropping a single sentence's final period in chat apps.
* **FR-28** A trailing space is added (configurable) so consecutive dictations join naturally.

### Personalization, history, settings (FR-29…FR-33)
* **FR-29** Personal dictionary: preferred spellings + phrase replacements, also given to the LLM.
* **FR-30** Local history (SQLite) of raw and final text, app, duration, latency; retention days; can be disabled. Window titles are not stored.
* **FR-31** Settings in a commented TOML file, validated with precise error messages, hot-reloaded at the next dictation.
* **FR-32** Tray menu: copy last dictation, open settings, open data folder, pause/resume hotkey, quit.
* **FR-33** CLI: `setup`, `listen [--dry-run]`, `stop`, `doctor`, `devices`, `history`, `transcribe`, `bench`, `autostart`.

### Extras (FR-34…FR-44, v0.2)
* **FR-34 Speak-to-edit:** with text selected in any app, holding the edit chord (default `Ctrl+Win+Alt`) records a spoken instruction; on release the selection is rewritten by the local LLM and replaced (one paste, undoable). Adding the extra key while holding the dictation chord switches the running session to edit mode.
* **FR-35** The selection is read through UI Automation; if the app has no text support, by copying (`copy_keys`, `Ctrl+Insert` in terminals) with the clipboard restored. Code editors require a UIA-confirmed selection (their copy grabs the whole line when nothing is selected).
* **FR-36** Rewrites are guarded: preambles/quotes stripped, refusals and prompt leaks rejected, the selection's outer whitespace preserved; the selection length is limited by the LLM context.
* **FR-37 Smart spacing:** before inserting, the text around the caret is read (UI Automation); casing (lowercase common words mid-sentence; never "I", names, acronyms), separating space, list on its own line, no doubled punctuation, no period when the sentence continues after the cursor. Unsupported apps fall back to v1 behaviour.
* **FR-38** Password fields are detected through UI Automation (browsers included) and never receive text.
* **FR-39 Multilingual:** `asr.language` = `en` (English model), `auto` or a locale code (multilingual model, 40 locales).
* **FR-40** Cleanup rules are applied per sentence language: English-only rules (times, lists, corrections resolver, question marks, "i"→"I") only for English; language-specific fillers and voice commands for es/fr/de/it/pt/nl.
* **FR-41** Non-English sentences use the LLM only when they contain a correction/filler cue (or `llm_mode = "always"`), behind a strict guard (no invented words, no statement turned into a question).
* **FR-42 Translation:** `cleanup.translate_to` writes every dictation in the target language (sentences already in it are only cleaned).
* **FR-43 GPU:** `llm.gpu` = `off` / `auto` / device name runs the LLM on a GPU through the Vulkan llama.cpp build; `auto` prefers a discrete GPU; failure falls back to the CPU automatically.
* **FR-44** `keynone doctor` reports GPUs, the GPU choice, the speech model for the language and UI Automation availability.

## 2.4 Non-functional requirements

| ID | Requirement | Target | Measured (Ryzen 5 5600H) |
|---|---|---|---|
| NFR-1 | Live lag (warm) | ≤ 1.0 s worst case | p50 0.40–0.67 s, max 0.81 s |
| NFR-2 | Release → text, no LLM needed | ≤ 0.5 s | 0.14–0.31 s |
| NFR-3 | Release → text, LLM needed | ≤ 1.5 s p95 | eval p95 0.38 s per unit |
| NFR-4 | Listener idle cost | 0 % CPU, < 50 MB | 0 ms CPU / 10 s, 22 MB private |
| NFR-5 | Engine memory | < 3 GB | ~2.0 GB |
| NFR-6 | CPU while dictating | leaves headroom on 6 cores | ASR RTF 0.30 on 4 threads |
| NFR-7 | Cold start | audio never lost | buffered; models ready in ~5 s |
| NFR-8 | Offline & private | no network after setup | llama-server bound to 127.0.0.1 + random key |
| NFR-9 | Contained install | all files under the project folder | portable layout (`KEYNONE_HOME`) |
| NFR-10 | Integrity | verify every download | pinned SHA-256 per file |
| NFR-11 | Robustness | a crash never orphans processes | kill-on-close job objects |
| NFR-12 | Quality gates | tests, lint, types | 250+ tests, ruff, mypy clean |
| NFR-13 | English unchanged by the extras | identical output to v1 | identical on all 6 test recordings; eval 34/34 |
| NFR-14 | Smart-spacing cost at insertion | ≤ 50 ms | ~7 ms per UIA query (after background warm-up) |
| NFR-15 | Speak-to-edit, 80-word paragraph | ≤ 5 s CPU, ≤ 1.5 s GPU | 2.9–3.5 s CPU, 0.5–0.7 s GPU |

## 2.5 Architecture

```
 ┌────────────────────── Listener (always on, tiny) ───────────────────────┐
 │ WH_KEYBOARD_LL hook ─► HotkeyMachine ─► worker ──► waveIn capture (16k) │
 │ tray icon / menu         (pure state machine)         │ 20 ms PCM frames │
 └───────────────────────────────────┬────────────────────┼─────────────────┘
        spawn on demand, kill-on-close job │ stdin: framed START/AUDIO/STOP │ stdout: JSON events
 ┌─────────────────────────────────── Engine (on demand) ──────────────────────────────────┐
 │ IPC reader ─► Session ─► Stream (sherpa-onnx Nemotron 560 ms) ─► SentenceSegmenter      │
 │                  │                                               │ finished sentences   │
 │                  ├─ Overlay (own UI thread, GDI)  ◄── preview ── CleanupPipeline        │
 │                  │                                    rules ─► resolver ─► LLM? ─► guard │
 │                  └─ Inserter (clipboard paste / SendInput) ─► History (SQLite)          │
 │ llama-server child (127.0.0.1, API key, prompt-prefix slot cache) ◄── LlmPolisher       │
 └─────────────────────────────────────────────────────────────────────────────────────────┘
```

**Lifecycle:** `IDLE` (listener only) → hotkey → engine spawned (or reused if
warm) → session → text inserted → engine kept for `keep_warm_seconds`, then
exits. `0` exits right after each session.

**Threads (engine):** IPC reader (main), session/ASR worker, cleanup worker,
overlay UI thread, model loaders. sherpa-onnx and llama.cpp release the GIL.

### IPC protocol
* listener → engine: `kind:u8 | len:u32le | payload` with kinds `S` START (JSON
  `{sid, mode, target_hwnd, insert}`), `A` AUDIO (PCM16), `E` STOP, `X` CANCEL,
  `M` MODE (JSON), `Q` QUIT.
* engine → listener: JSON lines — `hello`, `asr_ready`, `llm_ready`,
  `session_done {sid, status, latency_ms, live_lag_*…}`, `auto_stop`, `warning`.

## 2.6 Data & files (all under the project folder)

| Path | Content |
|---|---|
| `models/<asset>/` | Speech/LLM models (+ `.complete.json` with verified hashes) |
| `bin/llama.cpp-*/` | llama-server runtime |
| `data/config.toml` | User settings (copied from `src/keynone/default_config.toml`) |
| `data/history.db` | `dictations(id, created_at, app, raw_text, final_text, audio_seconds, latency_ms, llm_calls, method)` |
| `data/logs/` | Rotating logs; transcripts only if `privacy.log_transcripts = true` |
| `data/cache/` | LLM prompt-prefix slot files, generated icons and sounds |

The only optional exception is `keynone autostart enable` (Startup shortcut).

## 2.7 Security & privacy

* No telemetry, no network after setup; downloads pinned by SHA-256 over HTTPS.
* LLM server: loopback only, random port, random API key, dies with the engine.
* Keyboard hook processes keys in memory only; it ignores Keynone's own tagged input.
* Clipboard content is marked private (excluded from Win+V history and cloud sync).
* Password fields are skipped; window titles are not stored in history.

## 2.8 Acceptance criteria (v0.1)

| # | Criterion | Verification | Status |
|---|---|---|---|
| AC-1 | All four minimum features work on recorded speech | `keynone transcribe` on test audio; eval set | ✅ |
| AC-2 | Live lag ≤ 1 s (warm) | token-timestamp lag in `transcribe --realtime` | ✅ |
| AC-3 | Inserts into a real text box, clipboard restored | `tests/test_desktop_inject.py` | ✅ |
| AC-4 | Listener idle at 0 % CPU, engine absent until the hotkey | process sampling | ✅ |
| AC-5 | Engine started on demand, exits after the warm period | `tests/test_desktop_listener.py` | ✅ |
| AC-6 | Unit tests, lint, type check pass | `pytest`, `ruff`, `mypy` | ✅ |
| AC-7 | Real-voice check by the user (accent, microphone) | manual checklist in the plan | ⏳ |
| AC-8 | Smart spacing in a real text box | `tests/test_desktop_extras.py` | ✅ |
| AC-9 | Speak-to-edit rewrites a real selection with the local LLM | `tests/test_desktop_extras.py` | ✅ |
| AC-10 | Multilingual transcription + cleanup + translation | `keynone transcribe --language auto [--translate-to English]` on sample recordings | ✅ |
| AC-11 | GPU acceleration with CPU fallback | `keynone bench cleanup --gpu auto`, unit test for fallback | ✅ |
