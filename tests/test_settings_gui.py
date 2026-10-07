"""The settings window, driven the way a user would (it is never shown on screen).

Uses a throwaway copy of config.toml and a fake System, so nothing outside the
test's temporary folder is touched: no Startup shortcut, no terminal windows,
no messages to a running listener.
"""

from __future__ import annotations

import shutil
import time
import tkinter as tk
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from keynone.config import parse_config
from keynone.editions import BASE, Edition
from keynone.paths import Paths
from keynone.settings.app import SettingsApp
from keynone.settings.model import all_keys
from keynone.settings.system import System
from keynone.settings.theme import DARK, LIGHT
from keynone.settings.widgets import (
    ChoiceControl,
    HotkeyControl,
    NumberControl,
    ReplacementsControl,
    SnippetsControl,
    SwitchControl,
    WordsControl,
    chord_text,
    key_from_event,
)


class FakeSystem(System):
    def __init__(self, paths: Paths) -> None:
        super().__init__(paths)
        self.calls: list[tuple[Any, ...]] = []
        self.autostart_on = False

    def installed(self, asset_id: str) -> bool:
        return True

    def size_mb(self, asset_id: str) -> int:
        return 100

    def language_pack(self) -> bool:
        return False

    def edition(self) -> Edition:
        return BASE

    def gpu_runtime(self) -> bool:
        return False

    def microphones(self) -> list[str]:
        return ["Microphone (Realtek Audio)", "USB Headset"]

    def listener_running(self) -> bool:
        return True

    def settings_saved(self) -> bool:
        self.calls.append(("settings_saved",))
        return True

    def suspend_hotkeys(self, seconds: int) -> bool:
        self.calls.append(("suspend", seconds))
        return True

    def resume_hotkeys(self) -> bool:
        self.calls.append(("resume",))
        return True

    def autostart(self) -> bool:
        return self.autostart_on

    def set_autostart(self, enabled: bool) -> None:
        self.calls.append(("autostart", enabled))
        self.autostart_on = enabled

    def open(self, path: Path) -> None:
        self.calls.append(("open", path))

    def run_in_terminal(self, *args: str) -> None:
        self.calls.append(("terminal", *args))


@pytest.fixture(scope="module")
def _tk() -> Iterator[tk.Tk]:
    # One Tk for all tests: creating many interpreters in one process now and then
    # fails inside Tcl ('invalid command name "tcl_findLibrary"').
    try:
        window = tk.Tk()
    except tk.TclError as exc:  # pragma: no cover - no display
        pytest.skip(f"Tk unavailable: {exc}")
    window.withdraw()
    yield window
    window.destroy()


@pytest.fixture
def root(_tk: tk.Tk) -> Iterator[tk.Tk]:
    yield _tk
    for job in _tk.tk.splitlist(_tk.tk.call("after", "info")):
        _tk.after_cancel(job)  # e.g. the window's file watcher
    for child in _tk.winfo_children():
        child.destroy()


@pytest.fixture
def paths(tmp_path: Path) -> Paths:
    paths = Paths(tmp_path)
    paths.ensure()
    shutil.copyfile(paths.default_config, paths.config_file)
    return paths


def _app(root: tk.Tk, paths: Paths, palette: Any = LIGHT) -> tuple[SettingsApp, FakeSystem]:
    system = FakeSystem(paths)
    return SettingsApp(root, paths, palette=palette, system=system), system


def _saved(paths: Paths) -> Any:
    return parse_config(paths.config_file.read_text(encoding="utf-8"))


def _visit_all(app: SettingsApp) -> None:
    for section_id in list(app.nav):
        app.show_section(section_id)
        app.root.update_idletasks()


@pytest.mark.parametrize("palette", [LIGHT, DARK], ids=["light", "dark"])
def test_every_page_builds(root: tk.Tk, paths: Paths, palette: Any) -> None:
    app, _ = _app(root, paths, palette)
    app.show_advanced.set(True)
    app._apply_advanced()
    _visit_all(app)
    assert set(app.controls) == set(all_keys())  # every option of config.toml
    assert not app.problems and app._changes() == 0  # showing changes nothing


def test_editing_and_saving(root: tk.Tk, paths: Paths) -> None:
    app, system = _app(root, paths)
    _visit_all(app)
    assert "disabled" in app.save_button.state()

    cues = app.controls["audio.cues"]
    assert isinstance(cues, SwitchControl)
    cues.widget.invoke()  # click the switch
    warm = app.controls["lifecycle.keep_warm_seconds"]
    assert isinstance(warm, NumberControl)
    warm.var.set("60")
    warm._commit()
    mic = app.controls["audio.device"]
    assert isinstance(mic, ChoiceControl) and mic.combo is not None
    mic.combo.current(2)
    mic._picked()
    position = app.controls["overlay.position"]
    assert isinstance(position, ChoiceControl) and position.combo is None  # buttons
    position.var.set(position._token("top"))
    position._picked()
    assert app._changes() == 4
    assert "disabled" not in app.save_button.state()

    app.save()
    config = _saved(paths)
    assert config.audio.cues is False
    assert config.lifecycle.keep_warm_seconds == 60
    assert config.audio.device == "USB Headset"
    assert config.overlay.position == "top"
    assert ("settings_saved",) in system.calls
    assert app._changes() == 0
    assert "disabled" in app.save_button.state()
    text = paths.config_file.read_text(encoding="utf-8")
    assert "# Short start/stop sounds." in text  # comments survive


def test_invalid_input_blocks_saving_until_fixed(root: tk.Tk, paths: Paths) -> None:
    app, _ = _app(root, paths)
    app.show_section("privacy")
    opacity = app.controls["overlay.opacity"]
    assert isinstance(opacity, NumberControl)
    opacity.var.set("150")
    opacity._commit()
    assert "overlay.opacity" in app.problems
    assert "disabled" in app.save_button.state()
    app.save()  # refused
    assert _saved(paths).overlay.opacity == 0.94
    opacity.var.set("80")
    opacity._commit()
    assert not app.problems
    app.save()
    assert _saved(paths).overlay.opacity == 0.8


def test_hotkey_typed_and_recorded(root: tk.Tk, paths: Paths) -> None:
    app, system = _app(root, paths)
    dictate = app.controls["hotkeys.dictate"]
    edit = app.controls["hotkeys.edit"]
    assert isinstance(dictate, HotkeyControl) and isinstance(edit, HotkeyControl)
    dictate.var.set("ctrl+win+alt")  # the edit shortcut
    dictate._commit()
    assert list(app.problems) == ["hotkeys.dictate"]
    assert app.model.get("hotkeys.dictate") == "ctrl+win"  # invalid input stays out
    edit.var.set("ctrl+alt+e")  # moving the other shortcut away makes it valid
    edit._commit()
    assert not app.problems
    assert app.model.get("hotkeys.dictate") == "ctrl+win+alt"
    assert app.model.get("hotkeys.edit") == "ctrl+alt+e"
    dictate.var.set("f9")
    dictate._commit()
    assert not app.problems and app.model.get("hotkeys.dictate") == "f9"

    dictate._record()
    assert system.calls[-1][0] == "suspend"  # the listener ignores its hotkeys meanwhile

    def key(keysym: str, keycode: int) -> Any:
        return SimpleNamespace(keysym=keysym, keycode=keycode)

    dictate._key_down(key("Control_R", 17))
    dictate._key_down(key("Shift_R", 16))
    dictate._key_down(key("d", 0x44))
    dictate._key_up(key("d", 0x44))
    dictate._key_up(key("Shift_R", 16))
    assert dictate._recording  # a key is still held
    dictate._key_up(key("Control_R", 17))
    assert not dictate._recording
    assert ("resume",) in system.calls
    assert app.model.get("hotkeys.dictate") == "ctrl+shift+d"

    dictate._record()
    dictate._key_down(key("Escape", 0x1B))  # cancels, keeps the old shortcut
    assert app.model.get("hotkeys.dictate") == "ctrl+shift+d"


def test_dictionary_editors(root: tk.Tk, paths: Paths) -> None:
    app, _ = _app(root, paths)
    app.show_section("dictionary")
    words = app.controls["dictionary.words"]
    assert isinstance(words, WordsControl)
    words.text.insert("1.0", "Keynone\n  PostgreSQL \n\nKeynone\n")
    words._modified()
    assert app.model.get("dictionary.words") == ["Keynone", "PostgreSQL"]

    replacements = app.controls["dictionary.replacements"]
    assert isinstance(replacements, ReplacementsControl)
    replacements._add_row()
    heard, written, _ = replacements.rows[-1]
    heard.set("chat gpt")
    assert "dictionary.replacements" in app.problems  # one side is still empty
    written.set("ChatGPT")
    assert not app.problems
    app.save()
    config = _saved(paths)
    assert config.dictionary.words == ["Keynone", "PostgreSQL"]
    assert config.dictionary.replacements == {"chat gpt": "ChatGPT"}


def test_app_profiles(root: tk.Tk, paths: Paths) -> None:
    from keynone.settings.widgets import AppsControl

    app, _ = _app(root, paths)
    app.show_section("apps")
    apps = app.controls["apps"]
    assert isinstance(apps, AppsControl)
    apps._add()
    card = apps.cards[0]
    assert "apps" in app.problems  # no program or title yet
    card.name.set("notes")
    card.match.set("obsidian.exe, Notion.exe")
    card.style.set("chat")
    apps.changed()
    combo, _ = card.overrides["drop_final_period"]
    combo.current(2)  # "Leave out"
    apps.changed()
    assert not app.problems
    app.save()
    profile = _saved(paths).apps[0]
    assert (profile.name, profile.match, profile.style) == ("notes", ["obsidian.exe",
                                                                      "Notion.exe"], "chat")
    assert profile.drop_final_period is True


def test_language_settings_need_the_language_pack(root: tk.Tk, paths: Paths) -> None:
    app, system = _app(root, paths)
    app.show_section("speech")
    language = app.controls["asr.language"]
    assert "disabled" in language.widget.state()
    row = app.rows["asr.language"]
    assert row.note.winfo_manager() == "grid"
    row.note_button.invoke()
    assert system.calls[-1] == ("terminal", "setup", "--edition", "languages")


def test_revert_and_autostart(root: tk.Tk, paths: Paths) -> None:
    app, system = _app(root, paths)
    app.show_section("speech")
    app.autostart.set(True)
    app.controls["audio.cues"].widget.invoke()
    assert app._changes() == 2
    app.revert()
    assert app._changes() == 0 and app.model.get("audio.cues") is True
    app.autostart.set(True)
    app.save()
    assert ("autostart", True) in system.calls


def test_changes_made_in_a_text_editor_are_picked_up(root: tk.Tk, paths: Paths) -> None:
    app, _ = _app(root, paths)
    app.show_section("speech")
    time.sleep(0.02)
    text = paths.config_file.read_text(encoding="utf-8").replace("cues = true", "cues = false")
    paths.config_file.write_text(text, encoding="utf-8")
    app._watch()
    assert app.model.get("audio.cues") is False
    cues = app.controls["audio.cues"]
    assert isinstance(cues, SwitchControl) and cues.var.get() is False


def test_a_shortcut_written_differently_is_not_an_unsaved_change(root: tk.Tk,
                                                                 paths: Paths) -> None:
    text = paths.config_file.read_text(encoding="utf-8")
    paths.config_file.write_text(text.replace('dictate = "ctrl+win"', 'dictate = "Ctrl + Win"'),
                                 encoding="utf-8")
    app, _ = _app(root, paths)
    assert app._changes() == 0 and not app.problems
    dictate = app.controls["hotkeys.dictate"]
    assert isinstance(dictate, HotkeyControl)
    dictate.var.set("win+ctrl")  # the same keys: still nothing to save
    dictate._commit()
    assert app._changes() == 0
    dictate.var.set("f9")
    dictate._commit()
    assert app.model.changed_keys() == ["hotkeys.dictate"]


def test_an_unusable_shortcut_in_the_file_is_flagged(root: tk.Tk, paths: Paths) -> None:
    text = paths.config_file.read_text(encoding="utf-8")
    paths.config_file.write_text(text.replace('dictate = "ctrl+win"', 'dictate = "a"'),
                                 encoding="utf-8")
    app, _ = _app(root, paths)
    assert "hotkeys.dictate" in app.problems  # the config parses, but "a" would block typing
    app._refresh_all()
    assert "hotkeys.dictate" in app.problems


def test_revert_after_an_outside_edit_loads_the_file_as_it_is(root: tk.Tk,
                                                              paths: Paths) -> None:
    app, _ = _app(root, paths)
    app.show_section("speech")
    app.controls["audio.cues"].widget.invoke()  # an unsaved change ...
    time.sleep(0.02)
    text = paths.config_file.read_text(encoding="utf-8")
    paths.config_file.write_text(text.replace("normalize_level = true", "normalize_level = false"),
                                 encoding="utf-8")  # ... and an edit elsewhere
    app._watch()
    assert app.banner.winfo_manager() == "pack"  # told, not overwritten
    assert app.model.get("audio.normalize_level") is True
    app.revert()
    assert app.model.get("audio.normalize_level") is False
    assert app.model.get("audio.cues") is True
    assert app.banner.winfo_manager() == ""


def test_a_broken_file_is_shown_and_editing_is_blocked(root: tk.Tk, paths: Paths) -> None:
    paths.config_file.write_text("[hotkeys\n", encoding="utf-8")
    app, _ = _app(root, paths)
    assert app.banner.winfo_manager() == "pack"
    app.show_section("speech")
    assert "disabled" in app.controls["audio.cues"].widget.state()
    time.sleep(0.02)  # fixed in a text editor: the window recovers by itself
    shutil.copyfile(paths.default_config, paths.config_file)
    app._watch()
    assert app.model.error is None
    assert app.banner.winfo_manager() == ""
    assert "disabled" not in app.controls["audio.cues"].widget.state()


def test_recorded_keys_become_hotkey_names() -> None:
    assert key_from_event("Control_R", 17) == ("ctrl", "rctrl")
    assert key_from_event("Win_L", 91) == ("win", "lwin")
    assert key_from_event("F9", 0x78) == ("f9", "f9")
    assert key_from_event("Left", 0x25) is None  # arrows can't be hotkeys
    assert chord_text([("ctrl", "rctrl")]) == "rctrl"  # alone: the side matters
    assert chord_text([("win", "lwin"), ("ctrl", "lctrl")]) == "ctrl+win"
    assert chord_text([("ctrl", "lctrl"), ("alt", "lalt"), ("e", "e")]) == "ctrl+alt+e"


def test_snippets_page(root: tk.Tk, paths: Paths) -> None:
    app, _ = _app(root, paths)
    app.show_section("snippets")
    control = app.controls["snippets"]
    assert isinstance(control, SnippetsControl) and control.cards == []
    control._add()
    card = control.cards[0]
    card.phrase.set("My address")
    card.text.insert("1.0", "221B Baker Street\nLondon")
    card._modified()
    assert not app.problems
    control._add()
    control.cards[1].phrase.set("my  address")  # the same words again
    control.cards[1].text.insert("1.0", "x")
    control.cards[1]._modified()
    assert "twice" in app.problems["snippets"]
    control.remove(control.cards[1])
    assert not app.problems
    app.save()
    assert _saved(paths).snippets == {"My address": "221B Baker Street\nLondon"}
    text = paths.config_file.read_text(encoding="utf-8")
    assert '"My address" = """221B Baker Street' in text  # several lines, readable
    assert "# Text you insert by voice." in text  # the comments stay

    control._add()
    control.cards[-1].phrase.set("insert my email")
    control.cards[-1].text.insert("1.0", "jane@example.com")
    control.cards[-1]._modified()
    assert "Leave" in app.problems["snippets"]
    control.cards[-1].phrase.set("my email")
    assert not app.problems
    control.cards[-1].text.delete("1.0", "end")
    control.cards[-1]._modified()
    assert "no text" in app.problems["snippets"]
