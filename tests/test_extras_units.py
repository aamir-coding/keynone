"""Unit tests for the extras' plumbing: model choice, lead-in, GPU choice, fallback."""

from __future__ import annotations

import threading

import numpy as np

from keynone.asr import MULTILINGUAL_MODEL, Stream, effective_model
from keynone.cleanup.llm import _DEVICE_LINE_RE, GpuDevice, choose_gpu_device


def test_effective_model_switches_only_for_other_languages() -> None:
    assert effective_model("nemotron-en-560ms-int8", "en") == "nemotron-en-560ms-int8"
    assert effective_model("nemotron-en-560ms-int8", "en-GB") == "nemotron-en-560ms-int8"
    assert effective_model("nemotron-en-560ms-int8", "auto") == MULTILINGUAL_MODEL
    assert effective_model("nemotron-en-560ms-int8", "hi") == MULTILINGUAL_MODEL
    assert effective_model(MULTILINGUAL_MODEL, "en") == MULTILINGUAL_MODEL


class _FakeStream:
    def __init__(self) -> None:
        self.chunks: list[int] = []
        self.options: dict[str, str] = {}

    def accept_waveform(self, rate: int, samples: np.ndarray) -> None:
        self.chunks.append(len(samples))

    def set_option(self, key: str, value: str) -> None:
        self.options[key] = value


class _FakeRecognizer:
    def __init__(self) -> None:
        self.stream = _FakeStream()

    def create_stream(self) -> _FakeStream:
        return self.stream

    def is_ready(self, stream: object) -> bool:
        return False

    def is_endpoint(self, stream: object) -> bool:
        return False

    def get_result_all(self, stream: object) -> object:
        class Result:
            text = ""
            timestamps: list[float] = []

        return Result()


def test_english_streams_have_no_lead_in() -> None:
    rec = _FakeRecognizer()
    Stream(rec, threading.Lock()).accept_pcm16(b"\x00\x00" * 320)
    assert rec.stream.chunks == [320]  # exactly v1 behaviour


def test_multilingual_streams_get_a_lead_in_once() -> None:
    rec = _FakeRecognizer()
    stream = Stream(rec, threading.Lock(), lead_in_seconds=0.3)
    stream.set_language("hi")
    stream.accept_pcm16(b"\x00\x00" * 320)
    stream.accept_pcm16(b"\x00\x00" * 320)
    assert rec.stream.chunks == [4800, 320, 320]
    assert rec.stream.options == {"language": "hi"}


def test_device_line_parsing_and_discrete_gpu_preferred() -> None:
    output = """Available devices:
  Vulkan0: AMD Radeon(TM) Graphics (8112 MiB, 7706 MiB free)
  Vulkan1: NVIDIA GeForce GTX 1650 (4149 MiB, 3555 MiB free)"""
    devices = []
    for line in output.splitlines():
        m = _DEVICE_LINE_RE.match(line)
        if m:
            devices.append(GpuDevice(m.group(1), m.group(2), int(m.group(3))))
    assert [d.name for d in devices] == ["Vulkan0", "Vulkan1"]
    # The iGPU reports more (shared) memory; the discrete GPU must still win.
    assert choose_gpu_device(devices, "auto").name == "Vulkan1"
    assert choose_gpu_device(devices, "radeon").name == "Vulkan0"
    assert choose_gpu_device(devices, "intel arc") is None
    assert choose_gpu_device([], "auto") is None


def test_llm_falls_back_to_cpu_when_the_gpu_server_fails(monkeypatch) -> None:
    from keynone.config import Config
    from keynone.engine.models import EngineModels
    from keynone.paths import Paths

    config = Config()
    config.llm.gpu = "auto"
    models = EngineModels(config, Paths(home=__import__("pathlib").Path(".")), job=None)
    launched: list[str] = []

    class FakeServer:
        gpu_device = None

    class FakePolisher:
        server = FakeServer()

        def prime(self, *args: object) -> None:
            pass

        def warm_up_generation(self) -> None:
            pass

        def prime_rewrite(self) -> None:
            pass

        def prune_slot_files(self) -> int:
            return 0

    def fake_launch(gpu: str) -> FakePolisher:
        launched.append(gpu)
        if gpu != "off":
            raise RuntimeError("vulkan device lost")
        return FakePolisher()

    monkeypatch.setattr(models, "_launch_llm", fake_launch)
    models._start_llm()
    assert launched == ["auto", "off"]
    assert models.polisher is not None
    assert any("GPU unavailable" in error for error in models.errors)
    assert models.llm_ready.is_set()
