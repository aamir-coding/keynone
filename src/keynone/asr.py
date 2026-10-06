"""Streaming speech recognition: sherpa-onnx + NVIDIA Nemotron Speech Streaming.

The cache-aware transducer emits text chunk by chunk (560 ms) with native
punctuation and capitalization. Its output is append-only, which lets the
engine finalize and polish sentences while the user is still speaking.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from keynone.assets import get_asset
from keynone.editions import MULTILINGUAL_MODEL
from keynone.paths import Paths

log = logging.getLogger(__name__)

SAMPLE_RATE = 16_000
BYTES_PER_SECOND = SAMPLE_RATE * 2  # mono PCM16

MULTILINGUAL_MODELS = frozenset({MULTILINGUAL_MODEL})


def effective_model(model_id: str, language: str) -> str:
    """English-only models cannot transcribe other languages: use the multilingual one."""
    if language.split("-")[0].lower() != "en" and model_id not in MULTILINGUAL_MODELS:
        return MULTILINGUAL_MODEL
    return model_id


@dataclass(frozen=True)
class AsrModelFiles:
    encoder: Path
    decoder: Path
    joiner: Path
    tokens: Path


def model_files(model_id: str, paths: Paths) -> AsrModelFiles:
    directory = get_asset(model_id).directory(paths)

    def pick(stem: str) -> Path:
        for name in (f"{stem}.int8.onnx", f"{stem}.onnx"):
            if (directory / name).is_file():
                return directory / name
        raise FileNotFoundError(f"{stem} model missing in {directory} (run `keynone setup`)")

    tokens = directory / "tokens.txt"
    if not tokens.is_file():
        raise FileNotFoundError(f"tokens.txt missing in {directory} (run `keynone setup`)")
    return AsrModelFiles(pick("encoder"), pick("decoder"), pick("joiner"), tokens)


class Recognizer:
    """A loaded model (expensive: ~4.5 s on a Ryzen 5); create once, reuse."""

    def __init__(self, files: AsrModelFiles, *, threads: int, endpoint_silence: float,
                 multilingual: bool = False) -> None:
        import sherpa_onnx  # heavy import, deferred until needed

        self.multilingual = multilingual

        started = time.perf_counter()
        self._rec: Any = sherpa_onnx.OnlineRecognizer.from_transducer(
            tokens=str(files.tokens),
            encoder=str(files.encoder),
            decoder=str(files.decoder),
            joiner=str(files.joiner),
            num_threads=threads,
            sample_rate=SAMPLE_RATE,
            enable_endpoint_detection=True,
            rule1_min_trailing_silence=2.4,
            rule2_min_trailing_silence=endpoint_silence,
            rule3_min_utterance_length=3600.0,  # never force-split long dictations
        )
        self.load_seconds = time.perf_counter() - started
        # Serializes decoding when one dictation is finishing while the next begins.
        self.lock = threading.Lock()
        log.info("ASR model loaded in %.2fs (%s)", self.load_seconds, files.encoder.parent.name)

    def stream(self, language: str = "auto") -> Stream:
        """New dictation stream; ``language`` ("auto", "hi", "es-ES" ...) applies to
        multilingual models only."""
        # The lead-in recovers first words on the multilingual model; on the English
        # model it only shifts chunk boundaries (measured: slightly worse), so v1
        # behaviour is kept there.
        stream = Stream(self._rec, self.lock,
                        lead_in_seconds=Stream.LEAD_IN_SECONDS if self.multilingual else 0.0)
        if self.multilingual:
            stream.set_language(language)
        return stream


class Stream:
    """One dictation: feed 16 kHz PCM16 audio, read the growing transcript."""

    # Silence appended at the end so the last chunk (incl. look-ahead) is decoded.
    TAIL_PADDING_SECONDS = 0.6
    # Silence fed before the first audio of multilingual streams: without it the
    # first word can be lost when speech starts right at the key press.
    LEAD_IN_SECONDS = 0.3

    def __init__(self, recognizer: Any, lock: threading.Lock | None = None, *,
                 lead_in_seconds: float = 0.0) -> None:
        self._rec = recognizer
        self._lock = lock or threading.Lock()
        self._stream = recognizer.create_stream()
        self._text = ""
        self._in_endpoint = False
        self.samples = 0
        self._lead_in = lead_in_seconds
        self._lead_in_pending = lead_in_seconds > 0
        # Audio time (s) at which the newest recognized token was spoken; with
        # live audio, "now - stream start - last_token_time" is the display lag.
        self.last_token_time = 0.0

    def set_language(self, language: str) -> None:
        """Language prompt for multilingual Nemotron ("auto" = detect)."""
        self._stream.set_option("language", language or "auto")

    @property
    def text(self) -> str:
        return self._text

    @property
    def seconds(self) -> float:
        return self.samples / SAMPLE_RATE

    def accept_pcm16(self, pcm: bytes) -> tuple[str, bool] | None:
        """Decode new audio. Returns ``(text, endpoint)`` when something changed.

        ``endpoint`` is True once per pause that follows recognized speech.
        """
        samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        self.samples += samples.size
        if self._lead_in_pending:  # after set_language(): options apply from the start
            self._lead_in_pending = False
            lead_in = np.zeros(int(self._lead_in * SAMPLE_RATE), dtype=np.float32)
            self._stream.accept_waveform(SAMPLE_RATE, lead_in)
        self._stream.accept_waveform(SAMPLE_RATE, samples)
        changed = self._decode()
        endpoint = bool(self._rec.is_endpoint(self._stream))
        fire = endpoint and not self._in_endpoint and bool(self._text)
        self._in_endpoint = endpoint
        if changed or fire:
            return self._text, fire
        return None

    def _decode(self) -> bool:
        with self._lock:
            while self._rec.is_ready(self._stream):
                self._rec.decode_stream(self._stream)
            result = self._rec.get_result_all(self._stream)
        text = str(result.text).strip()
        timestamps = list(result.timestamps)
        if timestamps:
            self.last_token_time = max(0.0, float(timestamps[-1]) - self._lead_in)
        if text != self._text:
            self._text = text
            return True
        return False

    def finish(self) -> str:
        """Flush the model's look-ahead and return the final transcript."""
        padding = np.zeros(int(self.TAIL_PADDING_SECONDS * SAMPLE_RATE), dtype=np.float32)
        self._stream.accept_waveform(SAMPLE_RATE, padding)
        self._stream.input_finished()
        self._decode()
        return self._text
