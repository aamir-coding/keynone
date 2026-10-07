"""Settings that apply without restarting the engine, and the tray menu's building blocks."""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from keynone.config import load_config
from keynone.engine.main import _Live
from keynone.ipc import EventWriter
from keynone.paths import Paths
from keynone.win32 import api
from keynone.win32.gui import MenuEntry, TrayIcon


def _paths(tmp_path: Path) -> Paths:
    paths = Paths(tmp_path)
    paths.ensure()
    shutil.copyfile(paths.default_config, paths.config_file)
    _edit(paths, ("smart_spacing = true", "smart_spacing = false"),
          ('edit = "ctrl+win+alt"', 'edit = ""'), ("learn = true", "learn = false"))
    return paths


def _edit(paths: Paths, *changes: tuple[str, str]) -> None:
    time.sleep(0.02)  # a newer timestamp than before
    text = paths.config_file.read_text(encoding="utf-8")
    for old, new in changes:
        assert old in text
        text = text.replace(old, new)
    paths.config_file.write_text(text, encoding="utf-8")


def test_the_engine_reads_saved_settings_at_the_next_dictation(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    live = _Live(load_config(paths), paths, EventWriter(None), overlay_enabled=False)
    try:
        assert live.uia is None  # nothing needs UI Automation in this configuration
        assert live.refresh().insert.method == "paste"  # unchanged: the same config
        history = live.history
        _edit(paths, ('method = "paste"', 'method = "type"'),
              ("retention_days = 30", "retention_days = 7"),
              ("spoken_punctuation = true", "spoken_punctuation = false"))
        config = live.refresh()
        assert config.insert.method == "type" and live.inserter.config.method == "type"
        assert config.cleanup.spoken_punctuation is False
        assert live.history is not history  # reopened with the new retention
        _edit(paths, ('method = "type"', 'method = "nonsense"'))
        assert live.refresh().insert.method == "type"  # a broken file keeps what works
    finally:
        live.close()


def test_tray_menu_items_map_to_their_actions() -> None:
    called: list[str] = []
    items = [
        ("Header", None),
        ("-", None),
        MenuEntry("Switch", lambda: called.append("switch"), checked=True),
        MenuEntry("Microphone", submenu=(
            MenuEntry("A", lambda: called.append("a"), checked=True, radio=True),
            MenuEntry("B", lambda: called.append("b"), radio=True))),
        ("Quit", lambda: called.append("quit")),
    ]
    menu = api.CreatePopupMenu()
    actions: list[object] = []
    try:
        TrayIcon._fill(menu, items, actions)  # type: ignore[arg-type]
    finally:
        api.DestroyMenu(menu)
    assert actions[0] is None  # the greyed-out header
    for action in actions[1:]:
        action()  # type: ignore[operator]
    assert called == ["switch", "a", "b", "quit"]  # command ids follow the menu order
