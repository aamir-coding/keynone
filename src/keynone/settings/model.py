"""Reads and changes ``data/config.toml`` for the settings window.

Changes go into the user's own file with tomlkit, so comments, the order of
keys and anything the window doesn't know about stay as they were. Only values
that differ from the file are written, and the new text is checked with
:func:`keynone.config.parse_config` before it replaces the file in one step.
The file stays the single source of truth: the window, a text editor and the
command line can be used side by side, in any order.
"""

from __future__ import annotations

import contextlib
import copy
import dataclasses
import os
import shutil
import tempfile
import time
import typing
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

import tomlkit
from tomlkit.items import AoT, Comment, Table

from keynone.config import AppProfile, Config, ConfigError, parse_config
from keynone.win32.keys import Chord, ChordError

APPS_KEY = "apps"
SNIPPETS_KEY = "snippets"
HOTKEY_KEYS = ("hotkeys.dictate", "hotkeys.edit", "hotkeys.cancel", "insert.paste_keys",
               "insert.copy_keys")
_OPTIONAL_HOTKEYS = ("hotkeys.edit", "hotkeys.cancel")
_TYPING_KEYS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789") | {"space", "enter", "tab"}
# Alone, these are the first key of countless shortcuts (Ctrl+C, Alt+Tab, Win+E ...).
_SHORTCUT_MODIFIERS = frozenset({"ctrl", "lctrl", "shift", "lshift", "alt", "lalt", "win", "lwin"})


def hotkey_problem(key: str, text: str, values: dict[str, Any]) -> str | None:
    """Why ``text`` can't be used for the hotkey setting ``key`` (None if it can)."""
    text = text.strip().lower()
    if not text:
        if key in _OPTIONAL_HOTKEYS:
            return None
        return "Choose a key or a combination."
    try:
        chord = Chord.parse(text)
    except ChordError as exc:
        message = str(exc).replace("hotkey", "shortcut")
        return message[:1].upper() + message[1:]
    if key in ("hotkeys.dictate", "hotkeys.edit") and len(chord.names) == 1:
        if chord.names[0] in _TYPING_KEYS:
            return "You type with this key. Pick another one, or add Ctrl, Alt or Win."
        if chord.names[0] in _SHORTCUT_MODIFIERS:
            return "This key starts many shortcuts. Try rctrl, or combine it with another key."
    if key in ("hotkeys.dictate", "hotkeys.edit"):
        other = "hotkeys.edit" if key == "hotkeys.dictate" else "hotkeys.dictate"
        try:
            if values.get(other) and Chord.parse(values[other]) == chord:
                return "Dictation and speak-to-edit need different shortcuts."
        except ChordError:
            pass
    if key == "hotkeys.cancel":
        try:
            if any(Chord.parse(values[k]).involves(code)
                   for k in ("hotkeys.dictate", "hotkeys.edit") if values.get(k)
                   for code in chord.key_codes()):
                return "The cancel key can't be part of a dictation shortcut."
        except ChordError:
            pass
    return None
_SECTIONS = tuple(f.name for f in dataclasses.fields(Config)
                  if f.name not in (APPS_KEY, SNIPPETS_KEY))
PROFILE_FIELDS = tuple(f.name for f in dataclasses.fields(AppProfile))


def _plain(value: Any) -> Any:
    return copy.deepcopy(value) if isinstance(value, list | dict) else value


def profile_to_dict(profile: AppProfile) -> dict[str, Any]:
    """An app profile as written in config.toml: unset (None) and empty fields left out."""
    out: dict[str, Any] = {}
    for name in PROFILE_FIELDS:
        value = getattr(profile, name)
        if value is None or value == []:
            continue
        out[name] = list(value) if isinstance(value, list) else value
    return out


def flatten(config: Config) -> dict[str, Any]:
    """``{"hotkeys.dictate": "ctrl+win", ..., "apps": [{...}, ...]}``"""
    values: dict[str, Any] = {}
    for section in _SECTIONS:
        part = getattr(config, section)
        for f in dataclasses.fields(part):
            values[f"{section}.{f.name}"] = _plain(getattr(part, f.name))
    values[SNIPPETS_KEY] = dict(config.snippets)
    values[APPS_KEY] = [profile_to_dict(p) for p in config.apps]
    return values


@cache
def defaults() -> dict[str, Any]:
    return flatten(Config())


def all_keys() -> list[str]:
    return list(defaults())


def field_type(key: str) -> Any:
    if key == SNIPPETS_KEY:
        return dict[str, str]
    section, _, name = key.partition(".")
    return typing.get_type_hints(type(getattr(Config(), section)))[name]


def coerce(key: str, value: Any) -> Any:
    """Bring a value from a widget into the type the config expects."""
    if key == APPS_KEY:
        return [{k: _plain(v) for k, v in profile.items() if v is not None and v != []}
                for profile in value]
    tp = field_type(key)
    origin = typing.get_origin(tp)
    if tp is bool:
        return bool(value)
    if tp is int:
        return int(value)
    if tp is float:
        return float(value)
    if origin is list:
        return [str(v) for v in value]
    if origin is dict:
        return {str(k): str(v) for k, v in value.items()}
    return str(value)  # str and Literal[...] fields


def _toml_text(value: str) -> Any:
    """A string for config.toml; several lines as a multi-line string."""
    return tomlkit.string(value, multiline=True) if "\n" in value else value


# --------------------------------------------------------------------------- TOML editing


def _insertion_index(body: list[tuple[Any, Any]]) -> int:
    """Where a new key belongs in a table: after its last key, or after the comment
    lines right below the header (so it doesn't land under an unrelated comment block
    that happens to sit at the end of the table)."""
    last = None
    for i, (key, item) in enumerate(body):
        if key is not None and not isinstance(item, Table | AoT):
            last = i
    if last is not None:
        return last + 1
    i = 0
    while i < len(body) and body[i][0] is None and isinstance(body[i][1], Comment):
        i += 1
    return i


def _put(table: Any, key: str, value: Any) -> None:
    if key in table:
        table[key] = value  # tomlkit keeps the comments around the old value
        return
    container = table.value if isinstance(table, Table) else table
    index = _insertion_index(container.body)
    if index < len(container.body):
        try:
            container._insert_at(index, key, value)
            return
        except (AttributeError, ValueError):  # a future tomlkit: appending is fine too
            pass
    table[key] = value


def _new_table() -> Table:
    table = tomlkit.table()
    table.add(tomlkit.nl())
    return table


def _table(parent: Any, name: str) -> Any:
    if name not in parent:
        parent[name] = _new_table()
    return parent[name]


def _toml_value(value: Any) -> Any:
    if isinstance(value, list):
        array = tomlkit.array()
        array.extend(value)
        if len(value) > 4 or sum(len(str(v)) + 4 for v in value) > 70:
            array.multiline(True)
        return array
    return value


def _apply(doc: tomlkit.TOMLDocument, key: str, value: Any) -> None:
    if key == APPS_KEY:
        if value:
            aot = tomlkit.aot()
            for profile in value:
                table = tomlkit.table()
                for name in PROFILE_FIELDS:
                    if name in profile:
                        table[name] = _toml_value(profile[name])
                aot.append(table)
            doc[APPS_KEY] = aot
        elif APPS_KEY in doc:
            del doc[APPS_KEY]
        return
    if key == SNIPPETS_KEY:
        table = _table(doc, SNIPPETS_KEY)
        for old in [k for k in table if k not in value]:
            del table[old]
        for phrase, text in value.items():
            if phrase not in table or table[phrase] != text:
                _put(table, phrase, _toml_text(text))
        return
    section, _, name = key.partition(".")
    table = _table(doc, section)
    if (section, name) == ("dictionary", "replacements"):
        sub = _table(table, "replacements")
        for old in [k for k in sub if k not in value]:
            del sub[old]
        for heard, written in value.items():
            _put(sub, heard, written)
        return
    _put(table, name, _toml_value(value))


def render(base_text: str, changes: dict[str, Any]) -> str:
    """``base_text`` with ``changes`` applied; raises :class:`ConfigError` if invalid."""
    try:
        doc = tomlkit.parse(base_text)
    except tomlkit.exceptions.TOMLKitError as exc:
        raise ConfigError(f"invalid TOML: {exc}") from exc
    for key, value in changes.items():
        _apply(doc, key, value)
    text = tomlkit.dumps(doc)
    parse_config(text)
    return text


# --------------------------------------------------------------------------- files


def _read(path: Path) -> tuple[str, str]:
    """(text, newline) - utf-8-sig: older Notepad versions save a BOM."""
    raw = path.read_bytes()
    newline = "\r\n" if b"\r\n" in raw else "\n"
    return raw.decode("utf-8-sig").replace("\r\n", "\n"), newline


def atomic_write(path: Path, text: str, newline: str = "\n") -> None:
    """Write a complete new file next to ``path``, then swap it in.

    A crash can't leave a half-written config behind. If an editor holds the file
    open without sharing (rare on Windows), retry briefly, then write in place.
    """
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline=newline) as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        for attempt in range(10):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt == 9:
                    break
                time.sleep(0.05)
        with path.open("w", encoding="utf-8", newline=newline) as fh:
            fh.write(text)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)


@dataclass
class SaveResult:
    changed: list[str]
    merged: bool  # config.toml had been edited elsewhere meanwhile; those edits were kept


class SettingsModel:
    """The values shown in the window, the values in the file, and the difference."""

    def __init__(self, path: Path, default_file: Path) -> None:
        self.path = path
        self.default_file = default_file
        self.error: str | None = None  # the file on disk can't be used as it is
        self._disk_text = ""
        self._mtime = 0.0
        self._saved: dict[str, Any] = {}
        self._values: dict[str, Any] = {}
        self.load()

    # ----------------------------------------------------------------- reading

    def load(self) -> None:
        """(Re)read the file. Unsaved changes are dropped."""
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(self.default_file, self.path)
        self._mtime = self._stat()
        self._disk_text, _ = _read(self.path)
        try:
            config = parse_config(self._disk_text)
            self.error = None
        except ConfigError as exc:
            config = Config()
            self.error = str(exc)
        self._saved = flatten(config)
        self._values = copy.deepcopy(self._saved)

    def _stat(self) -> float:
        try:
            return self.path.stat().st_mtime
        except FileNotFoundError:
            return 0.0

    def changed_on_disk(self) -> bool:
        """True if something else wrote config.toml since it was read."""
        return self._stat() != self._mtime

    # ----------------------------------------------------------------- values

    def get(self, key: str) -> Any:
        return self._values[key]

    def saved(self, key: str) -> Any:
        return self._saved[key]

    def set(self, key: str, value: Any) -> None:
        if key not in self._values:
            raise KeyError(key)
        self._values[key] = coerce(key, value)

    def changed_keys(self) -> list[str]:
        return [k for k, v in self._values.items() if v != self._saved[k]]

    @property
    def dirty(self) -> bool:
        return any(v != self._saved[k] for k, v in self._values.items())

    def revert(self) -> None:
        self._values = copy.deepcopy(self._saved)

    # ----------------------------------------------------------------- writing

    def preview(self) -> str:
        """The text the file would have after saving (validated)."""
        base = _read(self.path)[0] if self.path.exists() else self._disk_text
        return render(base, {k: self._values[k] for k in self.changed_keys()})

    def save(self) -> SaveResult:
        """Write the changed values into the file; raises :class:`ConfigError`."""
        if self.error is not None:
            raise ConfigError(f"config.toml has an error - fix it first: {self.error}")
        changed = self.changed_keys()
        if not changed:
            return SaveResult([], False)
        if self.path.exists():
            base, newline = _read(self.path)
        else:
            base, newline = self._disk_text, "\n"
        merged = base != self._disk_text
        text = render(base, {k: self._values[k] for k in changed})
        atomic_write(self.path, text, newline)
        self.load()
        return SaveResult(changed, merged)

    def restore_defaults(self) -> Path:
        """Replace config.toml with the commented defaults; the old file is kept as .bak."""
        backup = self.path.with_name(self.path.name + ".bak")
        if self.path.exists():
            shutil.copyfile(self.path, backup)
        text, _ = _read(self.default_file)
        atomic_write(self.path, text)
        self.load()
        return backup
