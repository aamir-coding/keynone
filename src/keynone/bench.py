"""Benchmarks and quality evaluation (``keynone bench ...``).

* ``cleanup`` - runs the held-out dictation set in ``evaldata/cleanup_eval.jsonl``
  through the real cleanup pipeline (rules + LLM + guard) and reports pass rate
  per category and end-of-dictation latency.
* ``asr``     - streams a WAV file through the speech model in 20 ms frames,
  exactly like the microphone path, and reports load time, real-time factor,
  per-chunk compute and the transcript.
"""

from __future__ import annotations

import json
import statistics
import time
import wave
from collections import defaultdict
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

import numpy as np

from keynone.cleanup import rules
from keynone.cleanup.pipeline import CleanupPipeline, CleanupSettings, Polisher
from keynone.config import LlmMode


@dataclass
class EvalCase:
    id: str
    category: str
    input: str
    must: list[Any]
    must_not: list[Any]


def load_cases(path: Path | None = None) -> list[EvalCase]:
    if path is None:
        text = resources.files("keynone.evaldata").joinpath("cleanup_eval.jsonl").read_text(
            encoding="utf-8"
        )
    else:
        text = path.read_text(encoding="utf-8")
    return [EvalCase(**json.loads(line)) for line in text.splitlines() if line.strip()]


def _contains(text: str, needle: str | list[str]) -> bool:
    options = needle if isinstance(needle, list) else [needle]
    lowered = text.lower()
    return any(option.lower() in lowered for option in options)


def check_case(case: EvalCase, output: str) -> list[str]:
    problems = [f"missing {m!r}" for m in case.must if not _contains(output, m)]
    problems += [f"contains {m!r}" for m in case.must_not if _contains(output, m)]
    return problems


def run_cleanup_eval(
    polisher: Polisher | None,
    mode: LlmMode,
    *,
    cases: list[EvalCase] | None = None,
    timeout: float = 6.0,
    verbose: bool = True,
) -> dict[str, Any]:
    cases = cases if cases is not None else load_cases()
    by_category: dict[str, list[bool]] = defaultdict(list)
    latencies: list[float] = []
    failures: list[str] = []
    for case in cases:
        pipeline = CleanupPipeline(CleanupSettings(llm_mode=mode, llm_timeout=timeout), polisher)
        for sentence in rules.split_sentences(case.input):
            pipeline.add_sentence(sentence)
        started = time.perf_counter()
        result = pipeline.finish(timeout + 1)
        latencies.append((time.perf_counter() - started) * 1000)
        problems = check_case(case, result.text)
        by_category[case.category].append(not problems)
        if problems:
            note = f" [rejected: {'; '.join(result.rejections)}]" if result.rejections else ""
            failures.append(f"{case.id}: {'; '.join(problems)} -> {result.text!r}{note}")
        if verbose:
            mark = "PASS" if not problems else "FAIL"
            print(f"  {mark} {case.id:24s} {latencies[-1]:6.0f} ms  {result.text!r}")
    passed = sum(sum(v) for v in by_category.values())
    summary = {
        "mode": mode,
        "passed": passed,
        "total": len(cases),
        "categories": {k: f"{sum(v)}/{len(v)}" for k, v in sorted(by_category.items())},
        "latency_ms": {
            "mean": round(statistics.mean(latencies)),
            "p95": round(sorted(latencies)[int(0.95 * (len(latencies) - 1))]),
            "max": round(max(latencies)),
        },
        "failures": failures,
    }
    return summary


# --------------------------------------------------------------------------- ASR


def _pcm_to_float(frames: bytes, width: int, channels: int) -> np.ndarray:
    if width == 1:
        samples = (np.frombuffer(frames, np.uint8).astype(np.float32) - 128.0) / 128.0
    elif width == 2:
        samples = np.frombuffer(frames, "<i2").astype(np.float32) / 32768.0
    elif width == 3:
        raw = np.frombuffer(frames, np.uint8).reshape(-1, 3).astype(np.int32)
        value = raw[:, 0] | (raw[:, 1] << 8) | (raw[:, 2] << 16)
        samples = np.where(value >= 1 << 23, value - (1 << 24), value).astype(np.float32)
        samples /= float(1 << 23)
    elif width == 4:
        samples = np.frombuffer(frames, "<i4").astype(np.float32) / float(1 << 31)
    else:
        raise ValueError(f"unsupported sample width: {width} bytes")
    return samples.reshape(-1, channels).mean(axis=1)


def resample(samples: np.ndarray, rate: int, target: int = 16_000) -> np.ndarray:
    """Resample with a windowed-sinc anti-aliasing filter when downsampling."""
    if rate == target or samples.size == 0:
        return samples.astype(np.float32)
    if rate > target:
        cutoff = 0.45 * target / rate  # cycles per source sample
        n = np.arange(101) - 50
        taps = 2 * cutoff * np.sinc(2 * cutoff * n) * np.hamming(101)
        samples = np.convolve(samples, taps / taps.sum(), mode="same")
    positions = np.arange(int(samples.size * target / rate)) * (rate / target)
    return np.interp(positions, np.arange(samples.size), samples).astype(np.float32)


def read_wav_mono16k(path: Path) -> bytes:
    """Any PCM WAV (8-32 bit, mono/stereo, any rate) as 16 kHz mono PCM16 bytes."""
    with wave.open(str(path)) as w:
        rate, width, channels = w.getframerate(), w.getsampwidth(), w.getnchannels()
        frames = w.readframes(w.getnframes())
    if (rate, width, channels) == (16_000, 2, 1):
        return frames
    samples = resample(_pcm_to_float(frames, width, channels), rate)
    return (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2").tobytes()
