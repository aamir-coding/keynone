import itertools

import numpy as np

from keynone.audio_level import LevelNormalizer

_RNG = np.random.default_rng(7)


def _tone(peak: float, seconds: float = 0.5, lead_in: float = 0.3) -> bytes:
    """A "voice" after a short, nearly silent lead-in (like a microphone after the key)."""
    t = np.arange(int(16_000 * seconds)) / 16_000
    voice = np.sin(2 * np.pi * 220 * t) * peak
    hiss = _RNG.normal(0, peak / 300 + 1e-5, int(16_000 * (lead_in + seconds)))
    signal = np.concatenate([np.zeros(int(16_000 * lead_in)), voice]) + hiss
    return (np.clip(signal, -1, 1) * 32767).astype(np.int16).tobytes()


def _peak(pcm: bytes) -> float:
    return float(np.abs(np.frombuffer(pcm, np.int16)).max()) / 32767


def _feed(normalizer: LevelNormalizer, pcm: bytes) -> bytes:
    """In 20 ms frames, like the microphone."""
    return b"".join(normalizer.process(pcm[i : i + 640]) for i in range(0, len(pcm), 640))


def test_normal_speech_is_untouched() -> None:
    pcm = _tone(0.3)
    assert _feed(LevelNormalizer(), pcm) == pcm


def test_moderately_quiet_speech_is_untouched() -> None:
    # About -34 and -39 dBFS: the recognizer copes, and a boost measurably hurt there.
    for peak in (0.02, 0.011):
        pcm = _tone(peak, seconds=1.5)
        assert _feed(LevelNormalizer(), pcm) == pcm


def test_the_gain_grows_smoothly_below_the_threshold() -> None:
    normalizer = LevelNormalizer()
    gains = [normalizer.wanted_gain(10 ** (db / 20)) for db in range(-30, -70, -1)]
    assert gains[:11] == [1.0] * 11  # down to -40 dBFS: nothing
    steps = [b / a for a, b in itertools.pairwise(gains)]
    assert max(steps) < 1.27  # never more than 2 dB more gain for 1 dB quieter speech
    assert gains[-1] == normalizer.max_gain


def test_slightly_quiet_speech_gets_a_slight_boost() -> None:
    normalizer = LevelNormalizer()
    _feed(normalizer, _tone(0.008, seconds=1.5))  # about -42 dBFS
    assert 1.3 < normalizer.gain < 1.8


def test_quiet_speech_is_boosted_to_a_usable_level() -> None:
    out = _feed(LevelNormalizer(), _tone(0.004, seconds=1.5))  # about -48 dBFS
    assert 0.016 < _peak(out[-6400:]) < 0.3  # now above -36 dBFS


def test_a_soft_first_consonant_of_normal_speech_is_not_lifted() -> None:
    hiss = _RNG.normal(0, 0.0015, 1600)  # 100 ms of a quiet "s" (peak about -46 dBFS) ...
    t = np.arange(16_000) / 16_000
    vowel = np.sin(2 * np.pi * 220 * t) * 0.3  # ... then speech at a normal level
    signal = np.concatenate([np.zeros(4800), hiss, vowel])
    signal += _RNG.normal(0, 1e-4, signal.size)
    pcm = (signal * 32767).astype(np.int16).tobytes()
    assert _feed(LevelNormalizer(), pcm) == pcm


def test_silence_is_not_pumped_up() -> None:
    normalizer = LevelNormalizer()
    pcm = (_RNG.normal(0, 0.0003, 16_000) * 32767).astype(np.int16).tobytes()
    out = _feed(normalizer, pcm)
    assert normalizer.gain == 1.0 and out == pcm


def test_sudden_loud_speech_after_quiet_speech_does_not_clip() -> None:
    normalizer = LevelNormalizer()
    _feed(normalizer, _tone(0.002, seconds=1.5))
    assert normalizer.gain > 10
    loud = _feed(normalizer, _tone(0.5, seconds=0.2, lead_in=0))
    assert normalizer.gain == 1.0
    assert _peak(loud) <= 0.51  # the first loud frame is not amplified


def test_gain_is_capped() -> None:
    normalizer = LevelNormalizer(max_gain_db=20)
    _feed(normalizer, _tone(0.002, seconds=2.0))
    assert normalizer.gain <= 10.001
