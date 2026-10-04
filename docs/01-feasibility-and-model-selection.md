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
| Disk | — | ~1.4 GB default models (+1.3 GB optional Qwen3.5 2B) |

## 1.6 Known limitations

* English only by default. NVIDIA's multilingual *Nemotron 3.5 ASR Streaming*
  (40 locales, sherpa-onnx exports exist) is the natural next step.
* Elevated (admin) windows can't receive synthetic input from a normal process
  (Windows UIPI): Keynone detects this and copies the text to the clipboard.
* Password fields are detected for classic Win32 controls only (browsers need
  UI Automation, planned).
* The first dictation after the engine unloads waits ~5 s for the model.
