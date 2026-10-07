# 1. Feasibility & open-source model selection

**Verdict: feasible, with one honest trade-off.** A local Typeless-style
dictation app runs comfortably on the target machine (AMD Ryzen 5 5600H, 6C/12T,
16 GB DDR4-3200, Windows 11, CPU only). Live text stays under the 1-second
requirement and the polished text lands ~0.15–0.8 s after the key is released.
The trade-off: the best open speech model takes ~5 s to load, so "zero
background footprint" and "instant first word" cannot both hold for the *first*
dictation after the engine has unloaded. Keynone resolves this with a tiny
dormant listener (0 % CPU, ~22 MB) plus a load-on-demand engine with a
configurable warm period (default 5 min; `0` = strict on-demand).

All numbers below were measured on the target machine (October 2026).

## 1.1 Requirements → verdict

| Requirement | Result (measured) | Status |
|---|---|---|
| Works in any app where the cursor is | Clipboard paste with clipboard restore (or Unicode typing); verified in a real Win32 text box incl. emoji | ✅ |
| Comfortable on Ryzen 5 / 16 GB | Engine: ~30 % of 4 threads while speaking, ~2.0 GB RAM loaded; listener idle: 0 % CPU, 22 MB | ✅ |
| Live text ≤ 1 s behind speech | Word→screen lag p50 0.40–0.67 s, p95 ≤ 0.81 s, max 0.81 s (warm) | ✅ |
| Not running in background unless activated | Only a dormant hook listener stays resident (0 ms CPU in 10 s idle, no mic, no models); the engine exits after the warm period (or immediately with `keep_warm_seconds = 0`) | ✅ (see trade-off) |
| Removes filler words | Rules (um/uh/er/hmm…) + LLM for soft fillers (like/you know) | ✅ |
| Removes repetition | Rules (stutters, restarted phrases) | ✅ |
| Auto-edits when you change your mind | Deterministic resolver + LLM fallback + "scratch that" | ✅ 12/12 eval |
| Auto-formats | Lists, numbered steps, bullets, times, punctuation, questions, paragraphs | ✅ |

## 1.2 Speech recognition (ASR)

Runtime: **sherpa-onnx 1.13.8** (Apache-2.0, ONNX Runtime 1.28 bundled, Windows
wheels, streaming + offline models, releases the GIL while decoding).

| Candidate | Load | Speed on 5600H | Notes | Decision |
|---|---|---|---|---|
| **NVIDIA Nemotron Speech Streaming EN 0.6B, 560 ms, int8** | 4.5–5.2 s | RTF **0.30** (4 threads), worst chunk 202 ms | Native punctuation & capitalization, cache-aware streaming, append-only output; ~6.9 % avg WER | **Default** |
| Nemotron EN 0.6B, 160 ms, int8 | 4.6 s | RTF 0.82 (4 t), 1.02 (6 t) | Better latency in theory but no CPU headroom left for the LLM | Rejected for this CPU |
| Kroko streaming Zipformer EN | 5.2 s | RTF 0.05 | ~1.4 s decode granularity → live text too coarse | Rejected |
| Moonshine v2 base / tiny (offline) | 1.5 / 0.95 s | 8 s audio → 0.33 / 0.23 s | Fast load, but offline-only in sherpa-onnx and fails on inputs > ~10–16 s (needs VAD chunking) | Future "instant cold-start" option |
| Whisper / Parakeet TDT (offline) | 3–5 s | not streaming | Live text would need repeated re-decoding | Not chosen |

Load-time investigation: converting the Nemotron encoder to ORT format did not
help (4.8 s vs 5.7 s); disabling graph optimization or weight pre-packing did
not either. The cost is inherent to initializing a 0.6B int8 graph on this CPU.

## 1.3 Cleanup language model (LLM)

Runtime: official **llama.cpp** Windows CPU build (b11398, MIT), run as a private
`llama-server` child process (localhost + random API key, kill-on-close job).
Prebuilt `llama-cpp-python` wheels for Windows are not available, and the server
gives explicit prompt-cache control (slot save/restore).

Held-out evaluation (`keynone bench cleanup`, 34 dictation cases: corrections,
soft fillers, "must not answer/translate/execute", plain text, lists):

| Model (Q4_K_M) | License | auto mode | always mode | Speed |
|---|---|---|---|---|
| **LFM2.5 1.2B Instruct** | LFM Open License v1.0 | **34/34**, p95 380 ms | 33/34 | ~43 tok/s |
| Qwen3.5 2B | Apache-2.0 | 34/34, p95 795 ms | **34/34**, p95 1.16 s | ~25 tok/s |
| Qwen3.5 0.8B | Apache-2.0 | 25/34 (misses corrections) | 25/34 | ~43 tok/s |

Key findings:

* **Prompt framing matters more than model size.** With a chat-style few-shot
  prompt both small models *executed* dictated instructions ("Translate this
  sentence into French" → French). A document-continuation framing
  (`Transcript: … / Cleaned: …`) fixed this (8/8 "keep" cases).
* **Deterministic first, LLM second.** A rule-based self-correction resolver
  (anchor/type alignment of "X, no, Y") handles most corrections instantly and
  can't hallucinate; the LLM runs only when cues remain (auto mode), behind an
  output guard that rejects answers, additions, and dropped content.
* The LLM is therefore off the critical path for most dictations: release→text
  141–312 ms in the end-to-end runs.

**Default:** LFM2.5 1.2B in `auto` mode. **Max-polish option:** Qwen3.5 2B in
`always` mode (`keynone setup --with qwen3.5-2b-q4km`).

## 1.4 Measured end-to-end results (warm engine, real-time playback)

Synthesized dictations (Piper TTS) streamed in 20 ms frames, exactly like the mic:

| Dictation | Output | Live lag p95 | Release→text |
|---|---|---|---|
| "Let's meet at three PM no wait four PM at the cafe on Main Street" | `Let's meet at 4 PM at the cafe on Main Street.` | (cold start) | 1.7 s cold |
| "Um so I was thinking ah that we we should push the launch…" | `So I was thinking that we should push the launch to next week.` | 0.72 s | 0.19 s |
| "…three things first, book the flights second reserve the hotel and third rent a car" | numbered list with intro | 0.80 s | 0.20 s |
| "Can you send me the quarterly report before Friday" | `…before Friday?` | 0.73 s | 0.30 s |
| "I'll call you tomorrow morning. (s)cratch that. I'll call you tonight" | `I'll call you tonight.` | 0.56 s | 0.16 s |
| 22 s update with "Thursday, actually make that Friday" | correction applied, punctuation fixed | 0.73 s | 0.14 s |

Cold start (first dictation after the engine unloaded): ASR ready after ~4.6–5.5 s
(audio is buffered from the key press, nothing is lost), LLM ready after ~1.8 s.

## 1.5 Resource budget

| State | CPU | RAM |
|---|---|---|
| Dormant listener (always) | 0 ms CPU in 10 s idle | ~22 MB private (32 MB working set) |
| Engine loaded, idle (warm period) | 0 % | ~2.0 GB working set (ASR 0.74 GB, llama-server 1.24 GB incl. mapped model) |
| Dictating | ~30 % of 4 threads (ASR) + LLM bursts | same |
| Disk | — | ~1.4 GB models (Keynone), ~2.0 GB with the language pack (Keynone + languages); +1.3 GB optional Qwen3.5 2B |

## 1.6 Extras (v0.2) — measured on the same machine

| Extra | Approach | Measured result |
|---|---|---|
| Multilingual speech | NVIDIA Nemotron 3.5 ASR Streaming 0.6B (40 locales incl. Hindi), sherpa-onnx per-stream `language` option (`auto` or a code) | Load 3.5 s, RTF 0.31 (4 threads), worst chunk 172–220 ms — same as English. Sample recordings in English, Spanish, German and French transcribed correctly with punctuation. A 0.3 s silence lead-in was needed to keep the first word when speech starts instantly (multilingual model only; the English model measurably did better without it) |
| Language-aware cleanup | Per-sentence language detection (script + function words); English rules only for English; per-language fillers ("äh", "euh") and correction cues; LLM only for sentences with a cue, behind a strict guard | Found and fixed a real failure: the 1.2B model rewrote a correct Spanish sentence and changed its meaning — now plain sentences keep the recognizer's text and the strict guard rejects invented words |
| Translation | Document-continuation prompt with a target-language label | Spanish/German/French samples translated to English; quality is limited by the 1.2B model (Qwen3.5 2B recommended) |
| Smart spacing | UI Automation (TextPattern) reads ~80 chars before / 16 after the caret on a dedicated MTA thread with hard timeouts | First call ~1 s (COM wrappers, done in the background at engine start), then ~7 ms. Verified in a real text box: "I think" + "We should go." → "I think we should go." |
| Speak-to-edit | Ctrl+Win+Alt; selection via UI Automation (clipboard copy as fallback), rewrite on a second llama-server slot so the cleanup cache stays intact, guarded, pasted over the selection | 80-word paragraph: 2.9–3.5 s on CPU, 0.5–0.7 s on GPU; verified end to end in a real text box |
| GPU | Official llama.cpp Vulkan build (32 MB, no CUDA install); `auto` prefers the discrete GPU (the iGPU reports more shared memory, so "largest memory" would pick the wrong one); automatic CPU fallback | GTX 1650: 136–138 tok/s vs 41 tok/s on CPU; cleanup eval p95 165 ms vs 486 ms; 33/34 vs 34/34 (one near-tie decision differs due to GPU floating point) |

## 1.7 Stress test of the core features (October 2026)

An adversarial corpus of **352 dictations** (`src/keynone/evaldata/dictation_stress*.jsonl`):
self-corrections and the phrases that only look like one ("no rush", "I'm
sorry"), voice commands, lists, fillers, repetitions, questions, and mixed or
formatting cases — 132 / 52 / 46 / 39 / 36 / 27 / 20 cases. 253 cases were used while fixing; 99
were written afterwards as a held-out check. `scripts/stress_test.py` runs every
case in four recognizer styles: with punctuation, as one run-on sentence, without
punctuation, and both. Then the cases were spoken with five Windows voices and
recognized by the real speech model, so the cleanup sees genuine recognizer
mistakes. That last step is scored tolerantly: the structure must be right, with
no more word errors than the recognizer itself made.

| Rules only (instant) | punctuated | run-on | no punctuation | neither |
|---|---|---|---|---|
| Before (v0.2 code), 244 cases | 181 | 178 | 146 | 151 |
| **After**, 244 cases | **244** | **242** | **229** | **231** |
| Before, 99 held-out cases | 77 | 76 | 62 | 66 |
| **After**, 99 held-out cases | **99** | **99** | **92** | **96** |

| Real speech (5 voices) | rules only (240 cases) | with LFM2.5 (249 cases) |
|---|---|---|
| Before | 173–182 | 179–187 |
| **After** | **225–234** | **232–241** |

With the language model on all 352 cases (LFM2.5, CPU): 348 / 346 / 327 / 333.
Qwen3.5 2B on the GPU scores the same within one case.

Speak-to-edit (24 instructions spoken with the same voices, e.g. "make it
shorter", "turn this into a list", "translate to Spanish"): LFM2.5 gets 18/24
right, with a median of 0.6 s on the CPU and 0.2 s on the GPU. Qwen3.5 2B gets
23/24 (0.4 s on the GPU), so it is the recommended model when speak-to-edit
matters.

What changed (details in the code comments and in
`tests/test_dictation_regressions.py`):

* **Corrections.** A small cue grammar ("no", "no wait", "sorry, I meant",
  "actually make that", "scratch that") with strengths. The replaced phrase is
  aligned by type: a time replaces a time, a weekday replaces a weekday. Cues that
  are probably not corrections are left for the language model to judge.
* **Lists.** "first … second …", "number one …", counted lists ("three things:
  a, b and c"), and lists recited with spoken numbers.
* **Pauses.** The recognizer splits sentences at pauses. Those fragments are
  re-joined, punctuation that arrives late is re-attached, and "scratch that"
  removes exactly what was said since the last pause.
* **Guard.** Language model output is aligned word by word against the input.
  Anything that drops content, answers the text or adds words is rejected, and
  the rule-based text is used instead.

**Quiet microphones** (`audio.normalize_level`). 48 of the recordings were played
at lower levels with realistic noise: room noise that gets quieter with the
speech, plus the converter's own noise at -90 dBFS. Word error rate of the speech
model, by median speech peak:

| Speech peak | -4 dBFS | -24 | -34 | -39 | -44 | -49 |
|---|---|---|---|---|---|---|
| No boost | 2.3 % | 0.5 % | 1.6 % | 2.5 % | 5.2 % | 12.3 % |
| **Boost (default)** | 2.3 % | 0.5 % | 1.6 % | **2.3 %** | **2.3 %** | **4.1 %** |

Down to -40 dBFS the audio passes through bit for bit. Below that the gain grows
smoothly, by 2 dB for each dB quieter, up to +30 dB. It only rises after the first
0.2 s of speech and never during silence. Two earlier designs lifted quiet speech
straight to a target level. Both made recognition *worse* where the level hovered
around their threshold (1.6 → 4.8 % at -34 dBFS, and 5.2 → 13 % at -44 dBFS): a
sudden 25-30 dB step mid-sentence confuses the recognizer more than a low level
does. With a noisy microphone (a fixed noise floor at -60 dBFS) the results are
the same with and without the boost: speech that barely stands out from the noise
is left alone.

## 1.8 Testing the v0.3 features (October 2026)

**Spoken punctuation.** 46 new stress cases (32 in the main set, 14 held out),
half of them sentences where the word must stay a word ("the Oxford comma", "a
period of three weeks", "a dash of salt", "quote unquote"):

| | punctuated | run-on | no punctuation | neither |
|---|---|---|---|---|
| Rules only, 46 cases | 46 | 46 | 46 | 46 |
| With LFM2.5, 46 cases | 46 | 46 | 46 | 46 |

Spoken by five voices through the recognizer: **228 of 230** right; both misses
are recognition errors ("end quote" heard as "in court", "dot org" as "dot
organization"). The recognizer drops a spoken "dash" altogether and often
"quote", so command lines take "hyphen" or "minus" (`git commit -m`).

**No regression.** On the 352 existing cases v0.3 gives exactly the output of
v0.2.0's cleanup code: rules only in all four recognizer styles, and the 1,200
real-speech recordings replayed rules only and with LFM2.5 (0 outputs changed;
measured by running both versions on the same recordings). `keynone bench
cleanup`: 34/34, p95 431 ms. The six reference recordings give their
documented output.

**Desktop.** Learning a corrected name works against real Win32 EDIT and
RichEdit boxes (the control family of Notepad) through UI Automation; the
paste-last shortcut works through the real keyboard hook (the app never sees
the key); snippets and the prompt style were checked with synthesized speech.
The idle listener stays at 19 MB private memory and one CPU tick a minute.

## 1.9 Known limitations

* English is the default; other languages use the multilingual model (slightly
  less accurate for English) and lean on rules more than the LLM.
* The recognizer leaves out a spoken "dash" and sometimes "quote"; Keynone can't
  put back what it never heard (say "hyphen" or "minus" for command options).
* Learning from corrections needs the app to expose its text through UI
  Automation (most do); it doesn't watch terminals or code-style apps.
* Elevated (admin) windows can't receive synthetic input from a normal process
  (Windows UIPI): Keynone detects this and copies the text to the clipboard.
* Smart spacing and password detection need UI Automation support in the target
  app (Win32 controls, Office, browsers and most modern apps have it); without
  it Keynone inserts exactly as v1 did.
* The first dictation after the engine unloads waits ~5 s for the model.
