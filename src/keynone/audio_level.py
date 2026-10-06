"""Input level normalization for quiet microphones.

The speech model expects speech at a normal recording level: measured on the
stress-test recordings, the word error rate stays below 4% while speech peaks
stay above about -36 dBFS, but reaches 28% around -44 dBFS and 78% (often an
empty transcript) around -49 dBFS - a distant or low-gain laptop microphone.

:class:`LevelNormalizer` follows the *speech* level of a dictation, not single
frames: a peak envelope that rises at once and decays over ``release_seconds``
(it remembers the loudest speech through pauses) and is only updated by frames
that stand out from the noise floor. Speech at a normal or moderately quiet
level (envelope above ``quiet``) passes untouched; quieter speech is lifted
towards ``target_peak`` by at most ``max_gain_db``. The gain only rises while
someone speaks, never during silence (so room noise is not pumped up between
words), and drops at once when speech gets louder, so it does not clip.
"""

from __future__ import annotations

import math

import numpy as np


class LevelNormalizer:
    """Streaming automatic gain for 16-bit PCM (one instance per dictation)."""

    def __init__(self, *, quiet: float = 0.014, target_peak: float = 0.25,
                 max_gain_db: float = 30.0, release_seconds: float = 20.0,
                 frame_seconds: float = 0.02, speech_ratio: float = 4.0) -> None:
        self.quiet = quiet  # speech peaks below this (about -37 dBFS) get lifted
        self.target_peak = target_peak
        self.max_gain = 10 ** (max_gain_db / 20)
        self.speech_ratio = speech_ratio  # a frame this much above the noise is speech
        self._release = math.exp(-frame_seconds / release_seconds)
        self._noise_rise = math.exp(frame_seconds / 4.0)  # the floor creeps up over ~4 s
        self._rise = 10 ** (1.5 / 20)  # the gain grows by at most 1.5 dB per speech frame
        self._frame = int(16_000 * frame_seconds)
        self.noise: float | None = None
        self.envelope = 0.0
        self.gain = 1.0

    def _wanted(self) -> float:
        if self.envelope == 0.0 or self.envelope >= self.quiet:
            return 1.0
        return min(self.max_gain, max(1.0, self.target_peak / self.envelope))

    def process(self, pcm: bytes) -> bytes:
        """Return ``pcm`` (mono PCM16) with the current gain applied."""
        samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        if samples.size == 0:
            return pcm
        out = np.empty_like(samples)
        changed = False
        for start in range(0, samples.size, self._frame):
            frame = samples[start : start + self._frame]
            rms = float(np.sqrt(np.mean(frame * frame)))
            peak = float(np.max(np.abs(frame)))
            if self.noise is None or rms < self.noise:
                self.noise = max(rms, 1e-6)
            else:
                self.noise *= self._noise_rise
            speech = rms > self.noise * self.speech_ratio
            self.envelope = max(peak, self.envelope) if speech else self.envelope * self._release
            wanted = self._wanted()
            if wanted < self.gain:
                self.gain = wanted  # louder speech: back off at once
            elif speech and wanted > self.gain:
                self.gain = min(wanted, self.gain * self._rise)
            if self.gain != 1.0:
                changed = True
            out[start : start + frame.size] = frame * self.gain
        if not changed:
            return pcm  # normal level: untouched
        return (np.clip(out, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()
