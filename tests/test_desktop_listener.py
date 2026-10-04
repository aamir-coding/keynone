"""Opt-in desktop test of the real listener session path (minus the key press).

Run with:  $env:KEYNONE_DESKTOP_TESTS = "1"; pytest tests/test_desktop_listener.py

Opens the default microphone for ~2 s (audio is streamed to the engine and
discarded, never saved), spawns the engine, and uses dry-run mode so nothing
is typed anywhere. Checks: capture delivers 20 ms frames, the engine starts on
demand, the session completes, and the engine exits afterwards (strict mode).
"""

from __future__ import annotations

import os
import threading
import time

import pytest

from keynone.config import load_config
from keynone.listener.hotkeys import Action
from keynone.listener.main import ListenerApp, missing_assets
from keynone.paths import get_paths
from keynone.win32.audio import AudioCapture

pytestmark = [
    pytest.mark.desktop,
    pytest.mark.models,
    pytest.mark.skipif(os.environ.get("KEYNONE_DESKTOP_TESTS") != "1",
                       reason="set KEYNONE_DESKTOP_TESTS=1 to run desktop tests"),
]


def test_microphone_capture_delivers_frames() -> None:
    frames: list[int] = []
    capture = AudioCapture(lambda pcm: frames.append(len(pcm)))
    capture.start()
    time.sleep(1.0)
    capture.stop()
    assert 35 <= len(frames) <= 70, len(frames)  # ~50 frames of 20 ms per second
    assert all(size == AudioCapture.FRAME_BYTES for size in frames[:-1])


def test_listener_session_dry_run() -> None:
    paths = get_paths()
    if missing_assets(load_config(paths), paths):
        pytest.skip("models missing")
    app = ListenerApp(paths, dry_run=True)
    app.config.lifecycle.keep_warm_seconds = 0  # strict on-demand for this test
    done = threading.Event()
    statuses: list[str] = []
    original = app._on_engine_event

    def spy(event: dict) -> None:
        if event.get("event") == "session_done":
            statuses.append(str(event.get("status")))
            done.set()
        original(event)

    app.engine._on_event = spy
    try:
        app._handle(Action.START)
        assert app.engine.alive
        time.sleep(2.0)
        app._handle(Action.STOP)
        assert done.wait(60), "engine never reported the session"
        assert statuses[0] in ("dry_run", "empty"), statuses
        # Strict mode: the listener tells the engine to exit after the session.
        app._handle(("session_done", app._active_sid))
        deadline = time.monotonic() + 15
        while app.engine.alive and time.monotonic() < deadline:
            time.sleep(0.1)
        assert not app.engine.alive
    finally:
        app.engine.quit()
        app.job.close()
