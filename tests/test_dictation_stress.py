"""The adversarial dictation stress set, rules only (no LLM, no audio).

Cases: src/keynone/evaldata/dictation_stress*.jsonl - what a person says, in
pause-separated chunks, and what they expect. Every case must come out right
when the recognizer punctuates; without punctuation (or without pauses) some
cases are ambiguous, so those pass counts are only kept from going down.
Details and per-case output: ``uv run python scripts/stress_test.py text``.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest


def _load_harness() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "scripts" / "stress_test.py"
    spec = importlib.util.spec_from_file_location("stress_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["stress_test"] = module
    spec.loader.exec_module(module)
    return module


stress = _load_harness()
CASES = [case for case in stress.load_cases("", "all") if not case.llm]
# Ambiguous without pauses (style "run"): which words "scratch that" removes, and
# whether a lowercase continuation after a pause starts a new sentence.
RUN_KNOWN = {"cmd-scratch-fragments", "fmt-pause-sentences"}


def _dictate(case: object, style: str) -> str:
    chunks = stress.recognizer_styles(case)[style]
    updates, final = stress.chunks_to_updates(chunks)
    settings = stress.CleanupSettings(llm_mode="off")
    return str(stress.run_updates(updates, final, settings, None, finish_timeout=5).text)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.id)
def test_punctuated_dictation(case: object) -> None:
    output = _dictate(case, "punct")
    assert stress.matches(case, output, "exact"), output
    if case.id not in RUN_KNOWN:  # type: ignore[attr-defined]
        output = _dictate(case, "run")
        assert stress.matches(case, output, "exact"), f"without pauses: {output!r}"


@pytest.mark.parametrize(("style", "minimum"), [("bare", 321), ("runbare", 327)])
def test_unpunctuated_dictation_does_not_regress(style: str, minimum: int) -> None:
    passed = sum(stress.matches(case, _dictate(case, style), stress.LEVEL_FOR_STYLE[style])
                 for case in CASES)
    assert passed >= minimum, f"{passed}/{len(CASES)} passed (was {minimum})"
