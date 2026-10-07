"""Saved prompt caches don't pile up: only recently used ones are kept."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from keynone.cleanup.llm import LlamaServer, LlmPolisher
from keynone.cleanup.prompts import PromptBuilder
from keynone.config import Config
from keynone.engine.models import EngineModels
from keynone.paths import Paths


def _polisher(slot_dir: Path) -> LlmPolisher:
    server = LlamaServer(exe=Path("llama-server.exe"), model=Path("model.gguf"), slot_dir=slot_dir)
    return LlmPolisher(server, PromptBuilder(family="chatml"))


def _cache(slot_dir: Path, name: str, days_old: float) -> Path:
    path = slot_dir / name
    path.write_bytes(b"kv")
    used = time.time() - days_old * 86_400
    os.utime(path, (used, used))
    return path


def test_old_and_surplus_caches_are_deleted(tmp_path: Path) -> None:
    polisher = _polisher(tmp_path)
    polisher._loaded_prefix = "prefix-current.bin"
    current = _cache(tmp_path, "prefix-current.bin", days_old=40)  # in use: always kept
    rewrite = _cache(tmp_path, polisher._rewrite_prefix_name(), days_old=40)  # in use too
    stale = _cache(tmp_path, "prefix-olddictionary.bin", days_old=20)
    recent = [_cache(tmp_path, f"prefix-recent{i}.bin", days_old=i) for i in range(9)]
    other = _cache(tmp_path, "notes.bin", days_old=99)  # not ours: never touched

    assert polisher.prune_slot_files() == 4  # the stale one and the 3 least recent
    assert current.exists() and rewrite.exists() and other.exists()
    assert not stale.exists()
    assert [p.exists() for p in recent] == [True] * 6 + [False] * 3


def test_nothing_to_do_without_a_cache_folder(tmp_path: Path) -> None:
    assert _polisher(tmp_path / "missing").prune_slot_files() == 0


def test_a_failing_cleanup_never_costs_the_llm(tmp_path: Path,
                                               monkeypatch: pytest.MonkeyPatch) -> None:
    class Server:
        gpu_device = None

    class Polisher:
        server = Server()

        def prime(self, *args: object) -> None:
            pass

        def prime_rewrite(self) -> None:
            pass

        def prune_slot_files(self) -> int:
            raise PermissionError("the cache folder is read-only")

    models = EngineModels(Config(), Paths(tmp_path), None)
    monkeypatch.setattr(models, "_launch_llm", lambda gpu: Polisher())
    models._start_llm()
    assert models.polisher is not None and not models.errors
