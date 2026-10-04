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

from keynone.asr import Recognizer, model_files
from keynone.cleanup.llm import LlmPolisher, build_polisher
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

    def polish(self, transcript: str, *, deadline: float | None = None,
               on_text: Callable[[str], None] | None = None):  # noqa: ANN201
        remaining = 30.0 if deadline is None else max(0.0, deadline - time.monotonic())
        if not self._models.llm_ready.wait(remaining) or self._models.polisher is None:
            return _Unavailable()
        return self._models.polisher.polish(transcript, deadline=deadline, on_text=on_text)


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
        return self.config.cleanup.llm_mode != "off"

    def start(self) -> None:
        threading.Thread(target=self._load_asr, name="load-asr", daemon=True).start()
        if self.llm_enabled:
            threading.Thread(target=self._start_llm, name="load-llm", daemon=True).start()
        else:
            self.llm_ready.set()

    def _load_asr(self) -> None:
        try:
            cfg = self.config.asr
            self.recognizer = Recognizer(model_files(cfg.model, self.paths), threads=cfg.threads,
                                         endpoint_silence=cfg.endpoint_silence_seconds)
        except Exception as exc:
            log.exception("speech model failed to load")
            self.errors.append(f"Speech model failed to load: {exc}")
        finally:
            self.asr_ready.set()

    def _start_llm(self) -> None:
        started = time.perf_counter()
        try:
            cfg = self.config.llm
            polisher = build_polisher(cfg.model, self.paths, threads=cfg.threads,
                                      context=cfg.context,
                                      dictionary_words=tuple(self.config.dictionary.words),
                                      job=self.job)
            if not polisher.server.wait_ready(60):
                raise RuntimeError("llama-server did not become ready")
            polisher.prime()
            self.polisher = polisher
            log.info("LLM ready in %.2fs", time.perf_counter() - started)
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
