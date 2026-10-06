"""Input level normalization for quiet microphones.

The speech model expects speech at a normal recording level: measured on the
stress-test recordings, the word error rate stays below 4% down to -30 dB but
reaches 28% at -40 dB and 78% (often an empty transcript) at -45 dB - a distant
or low-gain laptop microphone. :class:`LevelNormalizer` leaves normal levels
alone and boosts only speech too quiet for the model (peaks below ``quiet``),
by up to ``max_gain_db``; it never attenuates, holds its gain through silence
(noise is not pumped up) and drops it instantly when speech gets louder, so it
does not clip.
"""

from __future__ import annotations

import math

import numpy as np


class LevelNormalizer:
    """Streaming automatic gain for 16-bit PCM (one instance per dictation)."""

    def __init__(self, *, quiet: float = 0.012, target_peak: float = 0.1,
                 silence: float = 0.0015, max_gain_db: float = 30.0,
                 release_seconds: float = 4.0, frame_seconds: float = 0.02) -> None:
        self.quiet = quiet  # speech peaks below this are boosted (about -38 dBFS)
        self.target_peak = target_peak
        self.silence = silence  # frames below this don't move the gain
        self.max_gain = 10 ** (max_gain_db / 20)
        # Per-frame decay of the peak envelope: slow, so pauses keep the speech level.
        self._release = math.exp(-frame_seconds / release_seconds)
        self._rise = 10 ** (1 / 20)  # the gain grows by at most 1 dB per frame
        self._frame = int(16_000 * frame_seconds)
        self.envelope = 0.0
        self.gain = 1.0

    def process(self, pcm: bytes) -> bytes:
        """Return ``pcm`` (mono PCM16) with the current gain applied."""
        samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        if samples.size == 0:
            return pcm
        out = np.empty_like(samples)
        for start in range(0, samples.size, self._frame):
            frame = samples[start : start + self._frame]
            peak = float(np.max(np.abs(frame)))
            self.envelope = max(peak, self.envelope * self._release)
            if self.envelope >= self.quiet:
                wanted = 1.0
            else:
                wanted = min(self.max_gain, self.target_peak / max(self.envelope, 1e-5))
            if wanted < self.gain:
                self.gain = max(1.0, wanted)  # louder speech: back off at once
            elif peak >= self.silence:
                self.gain = min(wanted, self.gain * self._rise)
            out[start : start + frame.size] = frame * self.gain
        if self.gain == 1.0 and not np.any(out != samples):
            return pcm  # normal level: untouched
        return (np.clip(out, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()
