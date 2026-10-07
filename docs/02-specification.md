# 2. Keynone — Software Specification

Version 0.2 · Windows 10/11 x64

## 2.1 Purpose and scope

Keynone is a local, private voice-dictation utility in the spirit of Typeless:
the user holds a hotkey, speaks naturally, and polished text appears in
whichever application has the text cursor. All speech recognition and text
processing run on the local CPU (optionally the GPU) with open-weight models;
no network access is needed after the one-time model download. v0.1 was English
dictation; v0.2 adds the extras (FR-34…FR-44), the settings window (FR-45…FR-50)
and two editions, with and without the language pack (FR-51…FR-54). Out of
scope: mobile platforms, cloud sync.

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
* **FR-10a Quiet microphones:** speech peaking below -40 dBFS is lifted before recognition, smoothly (2 dB more gain per dB quieter, at most +30 dB) so the gain never jumps mid-sentence; louder speech passes bit for bit; the gain never rises during silence or within the first 0.2 s of speech, and drops at once when speech gets louder (`audio.normalize_level`, on by default; measured in doc 01 §1.7).

### Live preview (FR-11…FR-13)
* **FR-11** An overlay shows a level meter, status (Starting / Listening / Hands-free / Polishing / Inserted / errors), and the latest two lines of text, already cleaned by the instant rules.
* **FR-12** The overlay never takes keyboard focus, is click-through, topmost, DPI-aware, and appears on the monitor of the active window.
* **FR-13** The overlay can be disabled.

### Cleanup (FR-14…FR-22)
* **FR-14 Fillers:** remove non-lexical fillers (um, uh, er, hmm…) deterministically, together with the commas the pause put around them ("I was, uh, going" → "I was going"); "you know" / "like" / "basically" set off by commas are removed by rule, other soft fillers by the LLM.
* **FR-15 Repetition:** remove stutters ("I I"), restarted phrases of up to six words ("I want to go to the, I want to go to the store"); keep intentional doubles ("very very", "had had", "come on, come on", spoken digits).
* **FR-16 Self-correction:** "X, no / no wait / sorry / I mean / I meant / or rather / actually make that / correction / scratch that, Y" replaces the corrected span - across sentences too ("…the 12th. Actually, make it the 14th."). A bare "no" (recognizers drop commas) only swaps values of the same kind ("Monday no Tuesday", "two no three chairs") or repeats a short phrase. Values are matched by kind - numbers and times, days and dates, times of day ("tomorrow morning no tonight"), names - so the rest of the sentence stays ("I'll call you Monday about the budget, no, Tuesday"). Idioms are never corrections ("no rush", "no problem", "I'm sorry", "no, I didn't" after a question). Unresolvable cases go to the LLM.
* **FR-17 Voice commands:** "new line", "new paragraph" - standalone, at the start or end of a sentence, or inside it ("…opening paragraph, new paragraph this is…"), never as an ordinary noun phrase ("a new line of products"). "scratch that" / "delete that" / "no, scratch that" removes what was said since the last pause (or corrects it when a repair follows: "seven thirty, no scratch that, six thirty AM"); a command split by a pause is joined back; a break said first or last is kept. Tolerant of one recognition slip per word and of "cross that".
* **FR-18 Lists:** ordinal enumerations ("first… second… finally…", "number one…", "step 1…", "first of all"), spoken numbers ("one the basket two the blanket three…"), and enumerations announced by a count ("we need four things: the laptop, the charger, the mouse and the bag") become numbered lists with the intro as a heading; pauses after a marker ("Number one [pause] tickets") are handled; "bullet point" becomes `- ` items. Ordinals used as adjectives ("my first time", "our number one priority") are not markers.
* **FR-19 Formatting:** sentence punctuation for unpunctuated speech (incl. `?` for questions and tag questions: ", right", "shall we"), capitalization, times ("four thirty PM" → "4:30 PM"), dictionary spellings, short label lines without a period ("Best regards\nAamir"), the recognizer's "nor"/"know" for a spoken "no".
* **FR-20 LLM polishing** (`auto` / `always` / `off`): faithful cleanup only — never answers, translates or executes dictated text. In `auto` mode the LLM sees only units with an unresolved correction, a soft filler, a restarted phrase or a long run without punctuation.
* **FR-21 Guardrails:** LLM output is aligned word by word with its input and rejected (rule-based text used instead) if it deletes anything but fillers, cue words, repetitions or the words a correction replaces, drops what was said after the last correction, adds words (never a negation or a connective like "because"), looks like an assistant reply, loses a question mark, or exceeds the time budget (`llm_timeout_seconds`).
* **FR-22** Cleanup runs incrementally per unit while the user speaks.

### Insertion (FR-23…FR-28)
* **FR-23** Insert into the window focused at release time via clipboard paste (default) or Unicode typing (per app).
* **FR-24** The previous clipboard is restored after pasting unless it changed meanwhile; Keynone's temporary clipboard content is excluded from clipboard history and cloud sync.
* **FR-25** Never insert into password fields (Win32 detection); never type while the user still holds modifier keys.
* **FR-26** If insertion isn't possible (no text field, admin window, keys held), copy to the clipboard and say so in the overlay.
* **FR-27 App profiles:** per-process/title style (`default`/`chat`/`email`/`prompt`/`code`, FR-57), paste keys, trailing space, LLM mode, dropping a single sentence's final period in chat apps.
* **FR-28** A trailing space is added (configurable) so consecutive dictations join naturally.

### Personalization, history, settings (FR-29…FR-33)
* **FR-29** Personal dictionary: preferred spellings + phrase replacements, also given to the LLM.
* **FR-30** Local history (SQLite) of raw and final text, app, duration, latency; retention days; can be disabled. Window titles are not stored.
* **FR-31** Settings in a commented TOML file, validated with precise error messages. A running listener applies saved changes within about a second (it compares the file's timestamp once a second while idle, and reacts at once to a message from the settings window), never during a dictation: a new hotkey works without a dictation first. A loaded engine restarts only if a setting it uses changed. A file with an error keeps the last good settings and shows a tray warning.
* **FR-32** Tray menu: copy last dictation, settings window, edit config.toml, open data folder, pause/resume hotkey, quit.
* **FR-33** CLI: `setup [--edition base|languages]`, `listen [--dry-run]`, `stop`, `settings`, `doctor`, `devices`, `history`, `transcribe`, `bench`, `autostart`.

### Extras (FR-34…FR-44, v0.2)
* **FR-34 Speak-to-edit:** with text selected in any app, holding the edit chord (default `Ctrl+Win+Alt`) records a spoken instruction; on release the selection is rewritten by the local LLM and replaced (one paste, undoable). Adding the extra key while holding the dictation chord switches the running session to edit mode.
* **FR-35** The selection is read through UI Automation; if the app has no text support, by copying (`copy_keys`, `Ctrl+Insert` in terminals) with the clipboard restored. Code editors require a UIA-confirmed selection (their copy grabs the whole line when nothing is selected).
* **FR-36** The spoken instruction is cleaned like a dictation, self-corrections included ("make it shorter, no, more formal"). Rewrites are guarded: preambles/quotes stripped, refusals, prompt leaks, template placeholders ("[Your Name]"), descriptions of the edit ("The greeting has been added") and copies of the prompt's examples rejected, the selection's outer whitespace preserved; the selection length is limited by the LLM context.
* **FR-37 Smart spacing:** before inserting, the text around the caret is read (UI Automation); casing (lowercase common words mid-sentence; never "I", names, acronyms), separating space, list on its own line, no doubled punctuation, no period when the sentence continues after the cursor. Unsupported apps fall back to v1 behaviour.
* **FR-38** Password fields are detected through UI Automation (browsers included) and never receive text.
* **FR-39 Multilingual:** `asr.language` = `en` (English model), `auto` or a locale code (multilingual model, 40 locales).
* **FR-40** Cleanup rules are applied per sentence language: English-only rules (times, lists, corrections resolver, question marks, "i"→"I") only for English; language-specific fillers and voice commands for es/fr/de/it/pt/nl.
* **FR-41** Non-English sentences use the LLM only when they contain a correction/filler cue (or `llm_mode = "always"`), behind a strict guard (no invented words, no statement turned into a question).
* **FR-42 Translation:** `cleanup.translate_to` writes every dictation in the target language (sentences already in it are only cleaned).
* **FR-43 GPU:** `llm.gpu` = `off` / `auto` / device name runs the LLM on a GPU through the Vulkan llama.cpp build; `auto` prefers a discrete GPU; failure falls back to the CPU automatically.
* **FR-44** `keynone doctor` reports GPUs, the GPU choice, the speech model for the language and UI Automation availability.

### Settings window (FR-45…FR-50, v0.2)
* **FR-45** A settings window (`keynone settings`, *Settings...* in the tray menu, `Keynone-settings.cmd`) covers every option of `config.toml`, in plain language with a short explanation each, on seven pages (General, Microphone & speech, Cleanup, Typing into apps, Dictionary, App profiles, Display & privacy) plus About. Options few people need are behind *Show advanced settings*. One window at a time; opening it again brings it to the front.
* **FR-46** `config.toml` stays the single source of truth. The window edits the file in place - comments, order, inline notes and keys it doesn't know are kept - writes only the values that changed, validates the result with the same parser the app uses, and replaces the file atomically. Changes made in a text editor while the window is open are reloaded (or, with unsaved changes in the window, merged when saving). A file with an error is shown with the message; the window reloads it once fixed, or restores the defaults (keeping `config.toml.bak`).
* **FR-47** Input that can't be used is marked on the field with the reason - out of range, unknown key names, the dictation and edit shortcuts being equal, a shortcut that would block typing (a letter, or a lone Ctrl/Shift/Alt/Win) - and Save stays disabled until it is fixed. Changes apply on Save (`Ctrl+S`); Revert discards them; closing with unsaved changes asks.
* **FR-48** Shortcuts can be typed or recorded by pressing them. While recording, a running listener ignores its hotkeys (at most 60 s; resumed automatically), and Win/Alt releases are masked.
* **FR-49** What isn't a setting is shown rather than hidden: a model that isn't downloaded, the GPU runtime or the language pack come with a button that runs the same `keynone setup ...` command in a terminal window; language settings are disabled without the language pack; the About page shows version, edition, folders, installed models with sizes, runs `keynone doctor` and restores defaults. *Start Keynone when I sign in* manages the Startup shortcut.
* **FR-50** Light and dark palettes follow the Windows app theme (title bar included); Segoe UI; drawn for the screen's scaling (100-200 %); usable from the keyboard (Tab between controls with a visible focus, Space to switch, `Ctrl+Tab` between pages).

### Editions (FR-51…FR-54, v0.2)
* **FR-51** Two editions are built from one codebase: **Keynone** (English speech model, LFM2.5 1.2B, llama.cpp CPU; ~1.4 GB of models) and **Keynone + languages** (the same plus the *language pack*, the multilingual speech model; ~2.0 GB). The language pack is what FR-39…FR-42 need; everything else, speak-to-edit included, is in both.
* **FR-52** What an installation can do depends only on what is installed (`keynone.editions`): adding the pack (`keynone setup --edition languages`, or the settings window) turns a base install into the languages edition, removing it turns it back. A release download names its edition in `edition.txt`, which only decides what a plain `keynone setup` / `install.ps1` downloads (`-Edition` overrides it).
* **FR-53** Without the pack, settings that need it fall back instead of failing: `asr.language` other than `en` → English model and language; `cleanup.translate_to` → off; a multilingual `asr.model` → the English model. Each fallback is logged, shown once per engine start in the tray, and reported by `keynone doctor`; the settings window disables those options and offers to add the pack.
* **FR-54** Releases: a `vX.Y.Z` tag builds `keynone-X.Y.Z-windows.zip` and `keynone-X.Y.Z-windows-languages.zip` (identical files except `edition.txt`), `SHA256SUMS.txt` and release notes, reproducibly (sorted entries, commit date). The zips are the user version: tests, CI files and the stress-test data and scripts stay in the git repository, where `install.ps1 -Dev` sets up a development copy. Models are never inside a download: setup fetches them from their publishers and verifies the pinned SHA-256 digests. A newer zip can be extracted over an older install; `data/`, `models/` and `bin/` are kept.

### Version 0.3 (FR-55…)
* **FR-55 Spoken punctuation** (`cleanup.spoken_punctuation`, on by default, English): "comma", "period" / "full stop", "question mark", "exclamation mark/point", "colon", "semicolon", "ellipsis" / "dot dot dot", "open/close bracket" (also paren, parenthesis, square and curly), "open/close quote" / "end quote" / "unquote" (a bare "quote" only when a closing one follows; an opening quote the recognizer didn't hear goes after the verb of saying), "hyphen", "dash" (a spaced en dash; in the code and prompt styles a hyphen that sticks to the next word: "dash dash help" → `--help`), "em dash", "slash" / "forward slash" / "backslash", "underscore", "ampersand", "at sign", "dollar sign". The mark replaces the recognizer's own punctuation next to it and fixes the capital after it. A punctuation word stays a word after a determiner or a word that names it ("a comma", "the Oxford comma", "trial period", "type comma"), before a word that makes it a noun ("period of", "comma splice", "colon cancer"), and in the plural; "literal comma" always types the word. A dictation that is only a mark inserts it ("question mark" → `?`); a mark said after a pause ends the text before the pause; a spoken final period stays in chat apps; a dictated colon and commas keep an announced enumeration inline. LLM output that drops a spoken mark other than `,` `.` `?` is rejected.
* **FR-56 Spoken addresses:** "jane dot doe at example dot com" (also "at the rate", "at sign", underscores, hyphens, multi-part domains such as "co dot in") → `jane.doe@example.com`; "example dot com slash pricing", "www dot …", "https colon slash slash …" → web addresses; "main dot py" → file names with a known extension (in the code and prompt styles also "user dot name"). A word that can't be a name ("me", "work", "a") is never part of an address.
* **FR-57 Prompt style** (built in for terminals, code editors and AI apps - `claude.exe`, `chatgpt.exe`, windows titled Claude, ChatGPT, Gemini, Perplexity, Copilot, DeepSeek): each dictation is written as prose when it is a sentence and as said when it is a command line or an identifier ("git status", "npm run dev -d", "userName", "@main.py" - a program name first and no prose words, or a code token such as `-m`, `./`, `|`; "find the bug", "git is slow today" are prose): no sentence capital on the program, no closing period. Casing commands for the next one to four words: "camel case", "pascal case", "snake case", "kebab case", "constant case", "all caps" (one word), stopping at function words and words like "function" ("a snake case get user by id function" → `a get_user_by_id function`). "at main dot py" → `@main.py`, except after words like "look", "see", "is". The LLM follows the global mode in terminals (it used to be off there). The `code` style writes every dictation as said.
* **FR-58 Snippets** (`[snippets]`: phrase → text, at most 256; Settings → Snippets): a dictation that is only the phrase (or "insert / paste / type" + the phrase; one recognition slip per word of 4+ letters allowed) inserts the saved text exactly as written - no capitals, periods or polishing, several lines allowed. "insert <phrase>" inside a longer dictation puts the text there: the phrase becomes a placeholder the cleanup and the LLM can't change (LLM output that loses one is rejected), filled in after smart spacing; a sentence's period right after a text that has several lines or its own final punctuation is dropped. The phrase alone inside a sentence ("my email is down") is ordinary speech.
* **FR-59 History page:** the settings window lists the dictations in `history.db`, newest first, 50 at a time: the text, when, the app, the length and how it went in (speak-to-edit, copied rather than typed); search (any case, `%`/`_` taken literally), Copy, Delete, Clear history (confirmed). The page never creates `history.db`; with the history off it says so and links to Display & privacy; dictations made while it is open appear within a few seconds.
* **FR-60 Paste last dictation** (`hotkeys.paste_last`, default `Alt+Shift+V`, `""` = off): the listener pastes the newest dictation from the history again into the app in front - without the engine, through the same paste/type path and app profile as a dictation - once the hotkey's modifiers are released. Its key is swallowed (also its repeats and release); a chord that contains the dictation chord (`Ctrl+Win+V`) cancels the dictation its first keys started, but only within the hold threshold, never a dictation under way. With the history off or empty, a tray message says why. The shortcut must differ from the dictation and edit shortcuts.
* **FR-61 Home page** (the settings window opens on it): whether Keynone is running (with *Start Keynone* if not), the shortcuts in use as key caps, and statistics - words dictated (all and the last 7 days), time saved compared with typing at 40 words a minute, speaking speed, dictations and since when, and words per day for the last 14 days. The statistics are daily counts in `history.db` (no text; speak-to-edit not counted), kept past the history's retention, counted once for dictations kept from 0.2, reset by *Clear history*; with the history off nothing is counted and the page says so. One tip at a time about a lesser-known feature.
* **FR-62 Learning from corrections** (`dictionary.learn`, on by default): after a dictation is inserted, the engine reads that text box (UI Automation) about 4, 10, 18, 30, 45 and 60 s later. It aligns the dictation with the text (word diff); a replaced span of 1-3 words is learned when it fixes how a name or term was heard - a similar spelling that looks like a name ("Amid" → "Aamir", "Sara" → "Sarah"), more capitals ("github" → "GitHub") or joined words ("Chat GPT" → "ChatGPT") - and has stayed the same for two reads (not a word still being typed). Not learned: a different word, grammar ("there" → "their"), numbers, a sentence's capital, a lowercase word for a name unless nearly identical, more than three fixes at once (rewriting). Learned spellings live in `data/learned.db` and apply to later dictations: exact ones only where the recognizer wrote the same thing and not at a sentence start unless learned there ("Amid the noise" stays), casing and spacing fixes in any case. A tray message says what was learned; Settings → Dictionary lists the spellings with Remove / Remove all. Not watched: terminals, code-style apps, password fields, speak-to-edit; a new dictation stops the watch.
* **FR-63 Smart warm-up** (`lifecycle.smart_warm`, on by default): after a dictation the engine stays loaded `keep_warm_seconds` (300 s), extended to 15 minutes after 2 dictations within 30 minutes and to 30 minutes after 3 within an hour (a longer `keep_warm_seconds` wins). The extension ends when there has been no keyboard or mouse input for 10 minutes (never before `keep_warm_seconds`) and is not given while Windows battery saver is on. `keep_warm_seconds = 0` stays strict on-demand. The listener checks once a second while the engine is loaded and idle, never during a dictation (replaces the fixed timer).

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
| NFR-12 | Quality gates | tests, lint, types | 720+ tests (incl. the 343-case stress set and the settings window), ruff, mypy clean |
| NFR-13 | English unchanged by the extras | identical output to v1 | identical on all 6 test recordings; eval 34/34 |
| NFR-14 | Smart-spacing cost at insertion | ≤ 50 ms | ~7 ms per UIA query (after background warm-up) |
| NFR-15 | Speak-to-edit, typical instruction | ≤ 2 s CPU, ≤ 1 s GPU | median 0.6 s CPU, 0.2 s GPU (cached prompt) |
| NFR-16 | Cleanup quality on adversarial dictation | no regression | stress set: 343/343 punctuated (rules only); 94–98 % on real recognizer output (5 voices) |
| NFR-17 | English dictation in both editions | identical | same code and models; the English path never consults the pack (`asr.language = "en"` fixes every sentence to English) |
| NFR-18 | Settings window start-up | ≤ 2 s | window shown 1.2 s after launch; redraw of a page < 0.5 s |
| NFR-19 | Disk space | language features cost only those who use them; nothing else stored | zips 0.26 MB each; after setup ~1.5 GB (base) vs ~2.2 GB (languages): models + Python 60 MB + packages 92 MB, no developer tools, download cache cleared, prompt caches bounded |

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

**Settings window:** a separate, short-lived process (Tk). It shares nothing
with the listener but `data/config.toml`. After saving it posts `WM_APP+2` to
the listener's hidden tray window ("apply the file now"); while recording a
shortcut it posts `WM_APP+3` with a number of seconds (hotkeys ignored meanwhile,
`0` = resume). Neither message carries data.

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
| `data/config.toml` | User settings (copied from `src/keynone/default_config.toml`); `config.toml.bak` after *Restore the default settings* |
| `edition.txt` | Only in release downloads: `base` or `languages` (what a plain setup downloads) |
| `data/history.db` | `dictations(id, created_at, app, raw_text, final_text, audio_seconds, latency_ms, llm_calls, method)`; `daily(day, dictations, words, audio_seconds)` - counts only, for the statistics |
| `data/learned.db` | `learned(heard, written, at_start, exact, created_at)` - spellings learned from corrections (FR-62) |
| `data/logs/` | Rotating logs; transcripts only if `privacy.log_transcripts = true` |
| `data/cache/` | LLM prompt-prefix slot files (3-25 MB each; unused for 14 days → deleted, at most 8 kept), generated icons and sounds |

The only optional exception is `keynone autostart enable` (Startup shortcut).

## 2.7 Security & privacy

* No telemetry, no network after setup; downloads pinned by SHA-256 over HTTPS.
* LLM server: loopback only, random port, random API key, dies with the engine.
* Keyboard hook processes keys in memory only; it ignores Keynone's own tagged input.
* Clipboard content is marked private (excluded from Win+V history and cloud sync).
* Password fields are skipped; window titles are not stored in history.
* Learning from corrections reads the text box a dictation went into during the following minute only, through UI Automation, compares it in memory and keeps nothing but the two spellings it learns (never logged); not in terminals, code-style apps or password fields; off with `dictionary.learn = false`.
* The listener's window messages from other processes can only ask it to reread
  `config.toml` or to ignore the hotkeys for at most 60 s (never during a
  dictation); neither carries data. The settings window writes nothing but
  `config.toml` (and, when asked, the Startup shortcut).

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
| AC-12 | Adversarial dictation (fillers, repetitions, corrections, commands, lists) | `tests/test_dictation_stress.py`; `scripts/stress_test.py text/audio/edit` | ✅ |
| AC-13 | The settings window covers every option and edits `config.toml` without losing anything; the listener applies saved changes while idle | `tests/test_settings_*.py`, `tests/test_listener_reload.py` | ✅ |
| AC-14 | Both editions build from one tag; a base install runs without the language pack and falls back safely | `tests/test_editions.py`; `scripts/build_release.py --working-tree`; the base zip installed into an empty folder | ✅ |
