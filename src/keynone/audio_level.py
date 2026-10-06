"""Input level normalization for quiet microphones.

The speech model copes with a wide range of recording levels, but not with very
quiet speech. Measured on 48 stress-test recordings with realistic noise (room
noise that gets quieter with the speech, plus the converter's own noise), the
word error rate is 2-3 % while speech peaks stay above about -40 dBFS, 5 % at
-44 dBFS and 12 % at -49 dBFS: a distant or low-gain laptop microphone.

:class:`LevelNormalizer` follows the *speech* level of a dictation, not single
frames: a peak envelope that rises at once and decays over ``release_seconds``
(it remembers the loudest speech through pauses) and is only updated by frames
that stand out from the noise floor. Speech peaking at ``quiet`` (-40 dBFS) or
louder passes through bit for bit. Below that, the gain grows smoothly - by
``slope`` dB for every dB the speech is quieter, up to ``max_gain_db`` - so the
gain never jumps when the level hovers around the threshold. (Two earlier
designs that jumped straight to a target level made recognition *worse* for
moderately quiet speech: a sudden 25-30 dB step in the middle of a sentence
confuses the recognizer more than the low level does.)

The gain only rises after the first ``settle_seconds`` of speech (a soft first
consonant of normal speech is not lifted), only while someone speaks (room noise
is not pumped up between words), and drops at once when speech gets louder, so
it does not clip.
"""

from __future__ import annotations

import math

import numpy as np


class LevelNormalizer:
    """Streaming automatic gain for 16-bit PCM (one instance per dictation)."""

    def __init__(self, *, quiet: float = 0.01, slope: float = 2.0, max_gain_db: float = 30.0,
                 release_seconds: float = 20.0, frame_seconds: float = 0.02,
                 speech_ratio: float = 4.0, settle_seconds: float = 0.2) -> None:
        self.quiet = quiet  # speech peaking below this (-40 dBFS) gets lifted
        self.slope = slope
        self.max_gain = 10 ** (max_gain_db / 20)
        self.speech_ratio = speech_ratio  # a frame this much above the noise is speech
        self._release = math.exp(-frame_seconds / release_seconds)
        self._noise_rise = math.exp(frame_seconds / 4.0)  # the floor creeps up over ~4 s
        self._rise = 10 ** (1.5 / 20)  # the gain grows by at most 1.5 dB per speech frame
        self._settle_frames = round(settle_seconds / frame_seconds)
        self._frame = int(16_000 * frame_seconds)
        self.noise: float | None = None
        self.envelope = 0.0
        self.gain = 1.0
        self.speech_frames = 0

    def wanted_gain(self, envelope: float) -> float:
        """The gain for speech peaking at ``envelope``: 1 down to ``quiet``, then
        ``(quiet / envelope) ** slope`` - continuous - capped at ``max_gain``."""
        if envelope <= 0.0 or envelope >= self.quiet:
            return 1.0
        return min(self.max_gain, (self.quiet / envelope) ** self.slope)

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
            self.speech_frames += speech
            self.envelope = max(peak, self.envelope) if speech else self.envelope * self._release
            wanted = self.wanted_gain(self.envelope)
            if wanted < self.gain:
                self.gain = wanted  # louder speech: back off at once
            elif speech and wanted > self.gain and self.speech_frames >= self._settle_frames:
                self.gain = min(wanted, self.gain * self._rise)
            if self.gain != 1.0:
                changed = True
            out[start : start + frame.size] = frame * self.gain
        if not changed:
            return pcm  # normal level: untouched
        return (np.clip(out, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()
