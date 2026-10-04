"""Integration test: drive the real engine process over its stdin/stdout protocol.

Needs the downloaded models (``keynone setup``) and the synthesized test audio
(``scripts/make_test_audio.py``); skipped otherwise. Runs headless (no overlay)
and as a dry run, so nothing is typed or shown on the desktop.
"""

from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
import time
import wave

import pytest

from keynone.assets import get_asset
from keynone.config import load_config
from keynone.ipc import Msg, encode, encode_json, parse_event
from keynone.paths import get_paths

pytestmark = pytest.mark.models

PATHS = get_paths()
AUDIO = PATHS.data / "test_audio" / "correction.wav"


def _ready() -> bool:
    config = load_config(PATHS)
    return AUDIO.exists() and get_asset(config.asr.model).is_installed(PATHS)


@pytest.mark.skipif(not _ready(), reason="models or test audio missing")
def test_engine_session_over_ipc() -> None:
    proc = subprocess.Popen(
        [sys.executable, "-m", "keynone", "engine", "--no-overlay"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert proc.stdin is not None and proc.stdout is not None
    events: queue.Queue[dict] = queue.Queue()
    stdout = proc.stdout

    def reader() -> None:
        for line in stdout:
            event = parse_event(line)
            if event:
                events.put(event)

    threading.Thread(target=reader, daemon=True).start()

    def wait_for(name: str, timeout: float) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                event = events.get(timeout=0.5)
            except queue.Empty:
                continue
            if event["event"] == name:
                return event
        raise AssertionError(f"no {name!r} event within {timeout}s")

    try:
        wait_for("hello", 30)
        with wave.open(str(AUDIO)) as wav:
            pcm = wav.readframes(wav.getnframes())
        proc.stdin.write(encode_json(Msg.START, {"sid": 7, "insert": False}))
        for i in range(0, len(pcm), 640):  # audio arrives while the models load
            proc.stdin.write(encode(Msg.AUDIO, pcm[i : i + 640]))
        proc.stdin.write(encode(Msg.STOP))
        proc.stdin.flush()
        done = wait_for("session_done", 90)
        assert done["sid"] == 7
        assert done["status"] == "dry_run", json.dumps(done)
        assert done["audio_seconds"] > 4
        proc.stdin.write(encode(Msg.QUIT))
        proc.stdin.flush()
        assert proc.wait(timeout=20) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
