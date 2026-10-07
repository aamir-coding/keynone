"""The listener applies saved settings on its own, but never in the middle of a dictation."""

from __future__ import annotations

import dataclasses
import shutil
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from keynone.config import Config
from keynone.listener.main import ListenerApp, engine_restart_needed
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
    # read by the listener, or by the engine again for every dictation
    ("hotkeys", "dictate", "f9", False),
    ("hotkeys", "hold_threshold_ms", 250, False),
    ("lifecycle", "keep_warm_seconds", 0, False),
    ("audio", "cues", False, False),
    ("audio", "device", "USB", False),
    ("audio", "normalize_level", False, False),
    ("insert", "method", "type", False),
    ("cleanup", "spoken_punctuation", False, False),
    ("cleanup", "llm_mode", "off", False),  # a loaded LLM simply goes unused
    ("cleanup", "translate_to", "Spanish", False),
    ("overlay", "enabled", False, False),
    ("history", "enabled", False, False),
    ("dictionary", "replacements", {"a": "b"}, False),
    ("hotkeys", "edit", "", False),  # the LLM stays loaded for polishing
    # loaded when the engine starts
    ("asr", "language", "auto", True),  # the multilingual speech model
    ("asr", "model", "nemotron-en-160ms-int8", True),
    ("asr", "threads", 2, True),
    ("llm", "model", "qwen3.5-2b-q4km", True),
    ("llm", "gpu", "auto", True),
    ("dictionary", "words", ["Keynone"], True),  # part of the LLM's prompt
])
def test_only_what_the_engine_loads_restarts_it(section: str, name: str, value: object,
                                                restart: bool) -> None:
    old = Config()
    new = Config()
    setattr(getattr(new, section), name, value)
    assert engine_restart_needed(old, new) is restart
    assert dataclasses.asdict(old) == dataclasses.asdict(Config())  # the input is untouched


def test_turning_the_llm_on_restarts_an_engine_without_one() -> None:
    old = Config()
    old.cleanup.llm_mode, old.hotkeys.edit = "off", ""  # no LLM loaded
    new = Config()
    new.hotkeys.edit = ""
    assert engine_restart_needed(old, new)
    same_language_model = Config()
    same_language_model.asr.language = "auto"
    other = Config()
    other.asr.language = "hi"  # the same multilingual model: no restart
    assert not engine_restart_needed(same_language_model, other)


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


# --------------------------------------------------------------------------- quick toggles


def _entries(items: list[object]) -> dict[str, object]:
    from keynone.win32.gui import MenuEntry

    return {item.label: item for item in items if isinstance(item, MenuEntry)}


def test_tray_quick_toggles_change_config_toml(app: ListenerApp,
                                               monkeypatch: pytest.MonkeyPatch) -> None:
    from keynone.win32.gui import MenuEntry

    monkeypatch.setattr("keynone.listener.main.input_devices",
                        lambda: ["Microphone (Realtek Audio)", "Headset (USB Audio)"])
    entries = _entries(app._menu())
    punctuation = entries["Spoken punctuation"]
    assert isinstance(punctuation, MenuEntry) and punctuation.checked
    assert punctuation.action is not None
    punctuation.action()
    app._housekeeping()  # applied at once (the listener is idle)
    assert app.config.cleanup.spoken_punctuation is False
    text = app.paths.config_file.read_text(encoding="utf-8")
    assert "spoken_punctuation = false" in text and "# Spoken" in text  # comments stay
    assert not _entries(app._menu())["Spoken punctuation"].checked  # type: ignore[attr-defined]

    microphone = _entries(app._menu())["Microphone"]
    assert isinstance(microphone, MenuEntry)
    choices = {e.label: e for e in microphone.submenu}
    assert choices["Windows default"].checked and choices["Windows default"].radio
    action = choices["Headset (USB Audio)"].action
    assert action is not None
    action()
    app._housekeeping()
    assert app.config.audio.device == "Headset (USB Audio)"
    assert "Language" not in _entries(app._menu())  # only with the language pack


def test_polish_toggle_remembers_the_mode(app: ListenerApp) -> None:
    app._set_setting("cleanup.llm_mode", "always")
    app._housekeeping()
    app._polish_mode = "always"
    app._toggle_polish()
    app._housekeeping()
    assert app.config.cleanup.llm_mode == "off"
    app._toggle_polish()
    app._housekeeping()
    assert app.config.cleanup.llm_mode == "always"


def test_a_broken_file_is_not_overwritten_by_a_toggle(app: ListenerApp) -> None:
    balloons: list[str] = []
    app.tray.balloon = lambda title, text, warning=False: balloons.append(text)  # type: ignore[method-assign]
    path = app.paths.config_file
    path.write_text(path.read_text(encoding="utf-8") + "\n[broken", encoding="utf-8")
    app._set_setting("audio.cues", False)
    assert balloons and "Couldn't change it" in balloons[0]
    assert path.read_text(encoding="utf-8").endswith("[broken")
