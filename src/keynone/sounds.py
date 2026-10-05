"""Short audio cues, synthesized once into data/cache/sounds (no bundled assets)."""

from __future__ import annotations

import logging
import math
import struct
import wave
import winsound
from pathlib import Path

log = logging.getLogger(__name__)

_RATE = 44_100
_CUES: dict[str, list[tuple[float, float]]] = {
    "start": [(784.0, 0.055), (1175.0, 0.075)],
    "edit": [(988.0, 0.045), (1319.0, 0.045), (1568.0, 0.07)],
    "stop": [(1175.0, 0.055), (784.0, 0.075)],
    "cancel": [(440.0, 0.11)],
    "error": [(330.0, 0.09), (247.0, 0.14)],
}


def _synthesize(path: Path, notes: list[tuple[float, float]], volume: float = 0.16) -> None:
    frames = bytearray()
    for frequency, duration in notes:
        count = int(_RATE * duration)
        attack, release = 0.006 * _RATE, 0.025 * _RATE
        for i in range(count):
            envelope = min(1.0, i / attack, (count - i) / release)
            sample = volume * envelope * math.sin(2 * math.pi * frequency * i / _RATE)
            frames += struct.pack("<h", int(sample * 32767))
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(_RATE)
        out.writeframes(bytes(frames))


class Cues:
    def __init__(self, directory: Path, enabled: bool = True) -> None:
        self.enabled = enabled
        self._paths: dict[str, Path] = {}
        for name, notes in _CUES.items():
            path = directory / f"{name}.wav"
            if not path.exists():
                try:
                    _synthesize(path, notes)
                except OSError:
                    log.warning("could not create cue %s", path)
                    continue
            self._paths[name] = path

    def play(self, name: str) -> None:
        if not self.enabled or name not in self._paths:
            return
        try:
            winsound.PlaySound(
                str(self._paths[name]),
                winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT,
            )
        except RuntimeError:
            log.debug("could not play cue %s", name)
