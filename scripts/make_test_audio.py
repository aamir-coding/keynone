"""Generate dictation-style test recordings with the local Piper TTS voice.

    keynone setup --with piper-en-lessac-medium
    uv run python scripts/make_test_audio.py

Writes 16 kHz mono WAVs to data/test_audio/. Each scenario is a list of
spoken phrases separated by pauses, mimicking how people dictate.
"""

from __future__ import annotations

import wave

import numpy as np
import sherpa_onnx

from keynone.assets import get_asset
from keynone.paths import get_paths

SCENARIOS: dict[str, list[str]] = {
    "correction": ["Let's meet at three PM, no wait, four PM, at the cafe on Main Street."],
    "fillers": ["Um, so I was thinking, uh, that we we should push the launch to next week."],
    "list": ["For the trip we need three things.", "First, book the flights.",
             "Second, reserve the hotel.", "And third, rent a car."],
    "question": ["Can you send me the quarterly report before Friday?"],
    "scratch": ["I'll call you tomorrow morning.", "Scratch that.", "I'll call you tonight."],
    "long": [
        "Hi team, quick update on the mobile release.",
        "The new onboarding flow is finished and QA signed off yesterday.",
        "We still need to fix the crash on older Android phones, which should take about two "
        "days.",
        "Let's plan the launch for Thursday, actually make that Friday, so marketing has time to "
        "prepare.",
        "Thanks everyone for the hard work.",
    ],
}
PAUSE_SECONDS = 0.9
TARGET_RATE = 16_000


def resample(samples: np.ndarray, source_rate: int) -> np.ndarray:
    duration = len(samples) / source_rate
    target = np.linspace(0, len(samples) - 1, int(duration * TARGET_RATE))
    return np.interp(target, np.arange(len(samples)), samples).astype(np.float32)


def main() -> None:
    paths = get_paths()
    model_dir = get_asset("piper-en-lessac-medium").directory(paths)
    config = sherpa_onnx.OfflineTtsConfig(
        model=sherpa_onnx.OfflineTtsModelConfig(
            vits=sherpa_onnx.OfflineTtsVitsModelConfig(
                model=str(model_dir / "en_US-lessac-medium.onnx"),
                tokens=str(model_dir / "tokens.txt"),
                data_dir=str(model_dir / "espeak-ng-data"),
            ),
            num_threads=4,
        )
    )
    tts = sherpa_onnx.OfflineTts(config)
    out_dir = paths.data / "test_audio"
    out_dir.mkdir(parents=True, exist_ok=True)
    silence = np.zeros(int(PAUSE_SECONDS * TARGET_RATE), dtype=np.float32)
    for name, phrases in SCENARIOS.items():
        parts = [np.zeros(int(0.3 * TARGET_RATE), dtype=np.float32)]
        for phrase in phrases:
            audio = tts.generate(phrase, sid=0, speed=1.0)
            parts += [resample(np.asarray(audio.samples, dtype=np.float32), audio.sample_rate),
                      silence]
        pcm = (np.clip(np.concatenate(parts), -1, 1) * 32767).astype(np.int16)
        path = out_dir / f"{name}.wav"
        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(TARGET_RATE)
            wav.writeframes(pcm.tobytes())
        print(f"{path} ({len(pcm) / TARGET_RATE:.1f}s)")


if __name__ == "__main__":
    main()
