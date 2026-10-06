import numpy as np

from keynone.audio_level import LevelNormalizer


def _tone(peak: float, seconds: float = 0.5) -> bytes:
    t = np.arange(int(16_000 * seconds)) / 16_000
    return (np.sin(2 * np.pi * 220 * t) * peak * 32767).astype(np.int16).tobytes()


def _peak(pcm: bytes) -> float:
    return float(np.abs(np.frombuffer(pcm, np.int16)).max()) / 32767


def _feed(normalizer: LevelNormalizer, pcm: bytes) -> bytes:
    """In 20 ms frames, like the microphone."""
    return b"".join(normalizer.process(pcm[i : i + 640]) for i in range(0, len(pcm), 640))


def test_normal_speech_is_untouched() -> None:
    pcm = _tone(0.3)
    assert _feed(LevelNormalizer(), pcm) == pcm


def test_quiet_speech_is_boosted_to_a_usable_level() -> None:
    out = _feed(LevelNormalizer(), _tone(0.004, seconds=1.5))  # about -48 dBFS
    assert 0.07 < _peak(out[-6400:]) < 0.15


def test_silence_is_not_pumped_up() -> None:
    normalizer = LevelNormalizer()
    out = _feed(normalizer, _tone(0.0005, seconds=1.0))  # below the silence threshold
    assert normalizer.gain == 1.0 and _peak(out) < 0.001


def test_sudden_loud_speech_after_quiet_speech_does_not_clip() -> None:
    normalizer = LevelNormalizer()
    _feed(normalizer, _tone(0.004, seconds=1.5))
    assert normalizer.gain > 10
    loud = _feed(normalizer, _tone(0.5, seconds=0.2))
    assert normalizer.gain == 1.0
    assert _peak(loud) <= 0.51  # the first loud frame is not amplified


def test_gain_is_capped() -> None:
    normalizer = LevelNormalizer(max_gain_db=20)
    _feed(normalizer, _tone(0.002, seconds=2.0))
    assert normalizer.gain <= 10.001
