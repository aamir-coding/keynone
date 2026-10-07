"""The listener applies saved settings on its own, but never in the middle of a dictation."""

from __future__ import annotations

import dataclasses
import shutil
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from keynone.config import Config
from keynone.listener.main import ListenerApp, engine_settings_changed
from keynone.paths import Paths
from keynone.win32.keys import Chord


@pytest.fixture
def app(tmp_path: Path) -> Iterator[ListenerApp]:
    paths = Paths(tmp_path)
    paths.ensure()
    shutil.copyfile(paths.default_config, paths.config_file)
    listener = ListenerApp(paths)
    yield listener
    listener.job.close()


def _edit(app: ListenerApp, old: str, new: str) -> None:
    path = app.paths.config_file
    time.sleep(0.02)  # a newer timestamp than the copy
    path.write_text(path.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")
    app._next_config_check = 0.0


def test_a_new_hotkey_applies_without_a_dictation(app: ListenerApp) -> None:
    _edit(app, 'dictate = "ctrl+win"', 'dictate = "f9"')
    app._housekeeping()
    assert app.config.hotkeys.dictate == "f9"
    assert app.machine.chord == Chord.parse("f9")


def test_settings_wait_while_a_dictation_is_running_or_finishing(app: ListenerApp) -> None:
    app._session_open = True
    app._session_stopped = time.monotonic()  # released a moment ago, still being finished
    _edit(app, 'dictate = "ctrl+win"', 'dictate = "f9"')
    app._housekeeping()
    assert app.config.hotkeys.dictate == "ctrl+win"
    app._after_session(app._active_sid)  # the engine reported the dictation as done
    app._housekeeping()
    assert app.config.hotkeys.dictate == "f9"


def test_a_broken_file_keeps_the_last_good_settings(app: ListenerApp) -> None:
    balloons: list[str] = []
    app.tray.balloon = lambda title, text, warning=False: balloons.append(text)  # type: ignore[method-assign]
    _edit(app, 'dictate = "ctrl+win"', 'dictate = "ctrl+banana"')
    app._housekeeping()
    assert app.config.hotkeys.dictate == "ctrl+win"
    assert balloons and "banana" in balloons[0]


def test_suspending_the_hotkeys_keeps_the_users_pause(app: ListenerApp) -> None:
    app._on_suspend_request(10, 0)
    assert not app.machine.enabled
    app._on_suspend_request(0, 0)  # the settings window is done recording
    assert app.machine.enabled
    app._toggle_pause()
    app._on_suspend_request(10, 0)
    app._suspended_until = time.monotonic() - 1  # expired: the window never resumed us
    app._housekeeping()
    assert not app.machine.enabled  # still paused by the user
    app._toggle_pause()
    assert app.machine.enabled


def test_hotkeys_are_not_suspended_during_a_dictation(app: ListenerApp) -> None:
    app._session_open = True
    app._session_stopped = time.monotonic()
    app._on_suspend_request(10, 0)
    assert app.machine.enabled


@pytest.mark.parametrize(("section", "name", "value", "restart"), [
    ("hotkeys", "dictate", "f9", False),
    ("hotkeys", "hold_threshold_ms", 250, False),
    ("lifecycle", "keep_warm_seconds", 0, False),
    ("audio", "cues", False, False),
    ("audio", "device", "USB", False),
    ("hotkeys", "edit", "", True),  # the engine loads the LLM for speak-to-edit
    ("audio", "normalize_level", False, True),
    ("asr", "language", "auto", True),
    ("insert", "method", "type", True),
])
def test_only_engine_settings_restart_a_loaded_engine(section: str, name: str, value: object,
                                                       restart: bool) -> None:
    old = Config()
    new = Config()
    setattr(getattr(new, section), name, value)
    assert engine_settings_changed(old, new) is restart
    assert dataclasses.asdict(old) == dataclasses.asdict(Config())  # the input is untouched


# --------------------------------------------------------------------------- paste last


def _paste_with_fakes(app: ListenerApp, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    from keynone.engine.inject import InsertResult
    from keynone.win32.windows import WindowInfo

    inserted: list[str] = []

    class FakeInserter:
        def __init__(self, config: object) -> None:
            pass

        def insert(self, text: str, profile: object, target: object) -> InsertResult:
            inserted.append(text)
            return InsertResult("paste", True)

    monkeypatch.setattr("keynone.engine.inject.Inserter", FakeInserter)
    monkeypatch.setattr("keynone.win32.windows.window_info", lambda hwnd=None: WindowInfo(
        1, 1, "notepad.exe", "Untitled - Notepad", "Notepad", "Edit", False))
    app.cues.enabled = False
    return inserted


def test_paste_last_pastes_the_newest_dictation(app: ListenerApp,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    from keynone.engine.history import History
    from keynone.listener.hotkeys import Action

    history = History(app.paths.history_db)
    for text in (" First one. ", " Hello there. "):
        history.add(app="notepad.exe", title="", raw_text=text, final_text=text,
                    audio_seconds=1.0, latency_ms=100, llm_calls=0, method="paste")
    history.close()
    inserted = _paste_with_fakes(app, monkeypatch)
    app._handle(Action.PASTE_LAST)
    assert inserted == ["Hello there. "]


def test_paste_last_says_why_it_has_nothing(app: ListenerApp,
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    from keynone.listener.hotkeys import Action

    inserted = _paste_with_fakes(app, monkeypatch)
    balloons: list[str] = []
    app.tray.balloon = lambda title, text, warning=False: balloons.append(text)  # type: ignore[method-assign]
    app._handle(Action.PASTE_LAST)
    assert inserted == [] and "No dictations yet" in balloons[-1]
    app.config.history.enabled = False
    app._handle(Action.PASTE_LAST)
    assert inserted == [] and "history is off" in balloons[-1]


def test_the_engine_unloads_when_the_warm_period_ends(app: ListenerApp,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    import threading

    quit_called = threading.Event()
    monkeypatch.setattr(type(app.engine), "alive", property(lambda self: True))
    monkeypatch.setattr(app.engine, "quit", lambda wait=5.0: quit_called.set())
    monkeypatch.setattr("keynone.win32.api.user_idle_seconds", lambda: 0.0)
    monkeypatch.setattr("keynone.win32.api.battery_saver_on", lambda: False)
    app._after_session(app._active_sid)  # a dictation just finished
    now = time.monotonic()
    app._maybe_unload(now + 299)
    assert not quit_called.wait(0.2)
    app._next_warm_check = 0.0
    app._maybe_unload(now + 301)
    assert quit_called.wait(2)
