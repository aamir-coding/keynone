"""Loads the speech model and starts the LLM in parallel, in the background.

On a cold start the microphone audio is already flowing while the models
load; nothing is lost, the live text simply starts once the ASR is ready.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from keynone.asr import MULTILINGUAL_MODELS, Recognizer, effective_model, model_files
from keynone.cleanup.llm import Completion, LlmPolisher, build_polisher
from keynone.cleanup.prompts import PromptVariant
from keynone.config import Config
from keynone.paths import Paths
from keynone.win32.process import KillOnCloseJob

log = logging.getLogger(__name__)


@dataclass
class _Unavailable:
    text: str = ""
    truncated: bool = True


class LazyPolisher:
    """Waits (within the caller's deadline) for the LLM to come up."""

    def __init__(self, models: EngineModels) -> None:
        self._models = models

    def polish(self, transcript: str, *, variant: PromptVariant = "en", target: str = "English",
               deadline: float | None = None,
               on_text: Callable[[str], None] | None = None) -> Completion | _Unavailable:
        polisher = self._wait(deadline)
        if polisher is None:
            return _Unavailable()
        return polisher.polish(transcript, variant=variant, target=target, deadline=deadline,
                               on_text=on_text)

    def rewrite(self, instruction: str, text: str, *, deadline: float | None = None,
                on_text: Callable[[str], None] | None = None) -> Completion | _Unavailable:
        polisher = self._wait(deadline)
        if polisher is None:
            return _Unavailable()
        return polisher.rewrite(instruction, text, deadline=deadline, on_text=on_text)

    def _wait(self, deadline: float | None) -> LlmPolisher | None:
        remaining = 30.0 if deadline is None else max(0.0, deadline - time.monotonic())
        if not self._models.llm_ready.wait(remaining):
            return None
        return self._models.polisher


class EngineModels:
    def __init__(self, config: Config, paths: Paths, job: KillOnCloseJob | None) -> None:
        self.config = config
        self.paths = paths
        self.job = job
        self.recognizer: Recognizer | None = None
        self.polisher: LlmPolisher | None = None
        self.asr_ready = threading.Event()
        self.llm_ready = threading.Event()
        self.errors: list[str] = []
        self.lazy_polisher = LazyPolisher(self)

    @property
    def llm_enabled(self) -> bool:
        """The LLM is needed for polishing and/or speak-to-edit."""
        return self.config.cleanup.llm_mode != "off" or bool(self.config.hotkeys.edit)

    @property
    def asr_model(self) -> str:
        return effective_model(self.config.asr.model, self.config.asr.language)

    def start(self) -> None:
        threading.Thread(target=self._load_asr, name="load-asr", daemon=True).start()
        if self.llm_enabled:
            threading.Thread(target=self._start_llm, name="load-llm", daemon=True).start()
        else:
            self.llm_ready.set()

    def _load_asr(self) -> None:
        try:
            cfg = self.config.asr
            model = self.asr_model
            self.recognizer = Recognizer(model_files(model, self.paths), threads=cfg.threads,
                                         endpoint_silence=cfg.endpoint_silence_seconds,
                                         multilingual=model in MULTILINGUAL_MODELS)
        except FileNotFoundError as exc:
            log.exception("speech model missing")
            hint = (f" - run: keynone setup --with {self.asr_model}"
                    if self.asr_model in MULTILINGUAL_MODELS else "")
            self.errors.append(f"Speech model missing{hint} ({exc})")
        except Exception as exc:
            log.exception("speech model failed to load")
            self.errors.append(f"Speech model failed to load: {exc}")
        finally:
            self.asr_ready.set()

    def _prompt_variants(self) -> list[tuple[PromptVariant, str]]:
        """Prompt prefixes to warm up: the ones this configuration will use."""
        variants: list[tuple[PromptVariant, str]] = [("en", "English")]
        if self.config.asr.language.split("-")[0] != "en":
            variants.append(("multi", "English"))
        if self.config.cleanup.translate_to:
            variants.append(("translate", self.config.cleanup.translate_to))
        return variants

    def _launch_llm(self, gpu: str) -> LlmPolisher:
        cfg = self.config.llm
        polisher = build_polisher(cfg.model, self.paths, threads=cfg.threads,
                                  context=cfg.context,
                                  dictionary_words=tuple(self.config.dictionary.words),
                                  gpu=gpu, gpu_layers=cfg.gpu_layers, job=self.job)
        if not polisher.server.wait_ready(90):
            polisher.close()
            raise RuntimeError("llama-server did not become ready")
        return polisher

    def _start_llm(self) -> None:
        started = time.perf_counter()
        gpu = self.config.llm.gpu
        try:
            try:
                polisher = self._launch_llm(gpu)
            except Exception:
                if gpu.lower() in ("", "off", "none", "cpu"):
                    raise
                log.exception("LLM failed to start on the GPU; falling back to the CPU")
                self.errors.append("GPU unavailable for the language model; using the CPU")
                polisher = self._launch_llm("off")
            for variant, target in self._prompt_variants():
                polisher.prime(variant, target)
            polisher.prime("en")  # the most frequent prefix stays loaded
            self.polisher = polisher
            log.info("LLM ready in %.2fs (%s)", time.perf_counter() - started,
                     polisher.server.gpu_device or "CPU")
        except Exception as exc:
            log.exception("LLM failed to start; continuing with rule-based cleanup")
            self.errors.append(f"LLM unavailable ({exc}); using rule-based cleanup")
        finally:
            self.llm_ready.set()

    def wait_asr(self, timeout: float | None = None) -> Recognizer | None:
        self.asr_ready.wait(timeout)
        return self.recognizer

    def close(self) -> None:
        if self.polisher is not None:
            self.polisher.close()
