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


def read_wav_mono16k(path: Path) -> bytes:
    """Return 16 kHz mono PCM16 bytes (only that format is supported here)."""
    with wave.open(str(path)) as w:
        if w.getframerate() != 16000 or w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise ValueError(f"{path}: need 16 kHz mono 16-bit PCM")
        return w.readframes(w.getnframes())
