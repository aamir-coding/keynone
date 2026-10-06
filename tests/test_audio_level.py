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
    pcm = _tone(0.02)  # about -34 dBFS: the recognizer copes, a boost would only add risk
    assert _feed(LevelNormalizer(), pcm) == pcm


def test_quiet_speech_is_boosted_to_a_usable_level() -> None:
    out = _feed(LevelNormalizer(), _tone(0.004, seconds=1.5))  # about -48 dBFS
    assert 0.1 < _peak(out[-6400:]) < 0.3


def test_silence_is_not_pumped_up() -> None:
    normalizer = LevelNormalizer()
    pcm = (_RNG.normal(0, 0.0003, 16_000) * 32767).astype(np.int16).tobytes()
    out = _feed(normalizer, pcm)
    assert normalizer.gain == 1.0 and out == pcm


def test_sudden_loud_speech_after_quiet_speech_does_not_clip() -> None:
    normalizer = LevelNormalizer()
    _feed(normalizer, _tone(0.004, seconds=1.5))
    assert normalizer.gain > 10
    loud = _feed(normalizer, _tone(0.5, seconds=0.2, lead_in=0))
    assert normalizer.gain == 1.0
    assert _peak(loud) <= 0.51  # the first loud frame is not amplified


def test_gain_is_capped() -> None:
    normalizer = LevelNormalizer(max_gain_db=20)
    _feed(normalizer, _tone(0.002, seconds=2.0))
    assert normalizer.gain <= 10.001
