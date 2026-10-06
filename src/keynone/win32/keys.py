"""Key names, chord parsing and synthetic keyboard input (SendInput)."""

from __future__ import annotations

import ctypes
import time
from collections.abc import Iterable
from dataclasses import dataclass

from keynone.win32 import api

# Tag carried in dwExtraInfo of every event we inject, so our own keyboard
# hook can ignore it (without ignoring other software's injected keys).
INJECTED_TAG = 0x4B4E4E45  # "KNNE"

INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004

VK_SHIFT, VK_CONTROL, VK_MENU = 0x10, 0x11, 0x12
VK_LSHIFT, VK_RSHIFT, VK_LCONTROL, VK_RCONTROL, VK_LMENU, VK_RMENU = (
    0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5,
)
VK_LWIN, VK_RWIN = 0x5B, 0x5C
VK_RETURN, VK_TAB, VK_ESCAPE, VK_SPACE, VK_INSERT = 0x0D, 0x09, 0x1B, 0x20, 0x2D
VK_CAPITAL = 0x14
VK_MASK = 0xE8  # unassigned; used to stop Win/Alt releases from opening menus

MODIFIER_VKS = frozenset(
    {VK_SHIFT, VK_CONTROL, VK_MENU, VK_LSHIFT, VK_RSHIFT, VK_LCONTROL, VK_RCONTROL, VK_LMENU,
     VK_RMENU, VK_LWIN, VK_RWIN}
)
_EXTENDED = frozenset(
    {VK_RCONTROL, VK_RMENU, VK_LWIN, VK_RWIN, VK_INSERT, 0x2E, 0x21, 0x22, 0x23, 0x24, 0x25,
     0x26, 0x27, 0x28, 0x5D, 0x6F, 0x90}
)

# name -> virtual-key codes that satisfy it
_NAMED: dict[str, frozenset[int]] = {
    "ctrl": frozenset({VK_LCONTROL, VK_RCONTROL, VK_CONTROL}),
    "lctrl": frozenset({VK_LCONTROL}),
    "rctrl": frozenset({VK_RCONTROL}),
    "shift": frozenset({VK_LSHIFT, VK_RSHIFT, VK_SHIFT}),
    "lshift": frozenset({VK_LSHIFT}),
    "rshift": frozenset({VK_RSHIFT}),
    "alt": frozenset({VK_LMENU, VK_RMENU, VK_MENU}),
    "lalt": frozenset({VK_LMENU}),
    "ralt": frozenset({VK_RMENU}),
    "altgr": frozenset({VK_RMENU}),
    "win": frozenset({VK_LWIN, VK_RWIN}),
    "lwin": frozenset({VK_LWIN}),
    "rwin": frozenset({VK_RWIN}),
    "space": frozenset({VK_SPACE}),
    "enter": frozenset({VK_RETURN}),
    "tab": frozenset({VK_TAB}),
    "esc": frozenset({VK_ESCAPE}),
    "escape": frozenset({VK_ESCAPE}),
    "capslock": frozenset({VK_CAPITAL}),
    "insert": frozenset({VK_INSERT}),
    "scrolllock": frozenset({0x91}),
    "pause": frozenset({0x13}),
    "apps": frozenset({0x5D}),
    "menu": frozenset({0x5D}),
}
for _i in range(1, 25):
    _NAMED[f"f{_i}"] = frozenset({0x70 + _i - 1})
for _c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789":
    _NAMED[_c.lower()] = frozenset({ord(_c)})

_MODIFIER_NAMES = frozenset(
    "ctrl lctrl rctrl shift lshift rshift alt lalt ralt altgr win lwin rwin".split()
)


_ALIASES = frozenset({"escape", "menu", "altgr"})  # second names for the same key


def key_name(vk: int) -> str | None:
    """The hotkey name of one physical key ("a", "f9", "rctrl", "capslock"), or None."""
    for name, codes in _NAMED.items():
        if name not in _ALIASES and codes == frozenset({vk}):
            return name
    return None


class ChordError(ValueError):
    pass


@dataclass(frozen=True)
class Chord:
    """A key combination such as ``ctrl+win`` or ``capslock``."""

    names: tuple[str, ...]

    @staticmethod
    def parse(text: str) -> Chord:
        names = tuple(part.strip().lower() for part in text.split("+") if part.strip())
        if not names:
            raise ChordError("empty hotkey")
        for name in names:
            if name not in _NAMED:
                raise ChordError(f"unknown key {name!r} in hotkey {text!r}")
        if len(set(names)) != len(names):
            raise ChordError(f"duplicate key in hotkey {text!r}")
        return Chord(names)

    def satisfied_by(self, pressed: Iterable[int]) -> bool:
        """True when exactly this chord is held (no unrelated keys)."""
        pressed = set(pressed)
        used: set[int] = set()
        for name in self.names:
            hit = pressed & _NAMED[name]
            if not hit:
                return False
            used |= hit
        return pressed <= used

    def involves(self, vk: int) -> bool:
        return any(vk in _NAMED[name] for name in self.names)

    @property
    def modifier_only(self) -> bool:
        return all(name in _MODIFIER_NAMES for name in self.names)

    @property
    def needs_menu_mask(self) -> bool:
        """Releasing Win/Alt after a chord would open Start/menu bars."""
        return any(name.endswith(("win", "alt")) or name == "altgr" for name in self.names)

    def key_codes(self) -> list[int]:
        """One concrete vk per name, for sending the chord (e.g. ``ctrl+v``)."""
        preferred = {"ctrl": VK_LCONTROL, "shift": VK_LSHIFT, "alt": VK_LMENU, "win": VK_LWIN}
        return [preferred.get(n, min(_NAMED[n])) for n in self.names]

    def __str__(self) -> str:
        return "+".join(n.capitalize() if len(n) > 1 else n.upper() for n in self.names)


# --------------------------------------------------------------------------- SendInput


def _key_input(vk: int, up: bool) -> api.INPUT:
    flags = KEYEVENTF_KEYUP if up else 0
    if vk in _EXTENDED:
        flags |= KEYEVENTF_EXTENDEDKEY
    event = api.INPUT(type=INPUT_KEYBOARD)
    event.ki = api.KEYBDINPUT(vk, api.MapVirtualKeyW(vk, 0), flags, 0, INJECTED_TAG)
    return event


def _unicode_input(code_unit: int, up: bool) -> api.INPUT:
    flags = KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if up else 0)
    event = api.INPUT(type=INPUT_KEYBOARD)
    event.ki = api.KEYBDINPUT(0, code_unit, flags, 0, INJECTED_TAG)
    return event


def _send(events: list[api.INPUT]) -> None:
    if not events:
        return
    array = (api.INPUT * len(events))(*events)
    sent = api.SendInput(len(events), array, ctypes.sizeof(api.INPUT))
    if sent != len(events):
        raise OSError(f"SendInput injected {sent}/{len(events)} events (blocked by UIPI?)")


def send_chord(chord: Chord) -> None:
    """Press and release a chord, e.g. Ctrl+V."""
    codes = chord.key_codes()
    events = [_key_input(vk, False) for vk in codes]
    events += [_key_input(vk, True) for vk in reversed(codes)]
    _send(events)


def send_menu_mask() -> None:
    """Tap an unassigned key so a held Win/Alt no longer opens Start/menus."""
    _send([_key_input(VK_MASK, False), _key_input(VK_MASK, True)])


def type_text(text: str, *, newline: str = "enter", chunk: int = 64) -> None:
    """Type ``text`` as Unicode keystrokes (fallback when paste is unavailable).

    ``newline`` is ``"enter"`` or ``"shift+enter"`` (chat apps send on Enter).
    """
    events: list[api.INPUT] = []
    for char in text.replace("\r\n", "\n"):
        if char == "\n":
            if newline == "shift+enter":
                events += [_key_input(VK_LSHIFT, False), _key_input(VK_RETURN, False),
                           _key_input(VK_RETURN, True), _key_input(VK_LSHIFT, True)]
            else:
                events += [_key_input(VK_RETURN, False), _key_input(VK_RETURN, True)]
        elif char == "\t":
            events += [_key_input(VK_TAB, False), _key_input(VK_TAB, True)]
        else:
            data = char.encode("utf-16-le")
            for i in range(0, len(data), 2):
                unit = int.from_bytes(data[i : i + 2], "little")
                events += [_unicode_input(unit, False), _unicode_input(unit, True)]
    for i in range(0, len(events), chunk * 2):
        _send(events[i : i + chunk * 2])
        time.sleep(0.005)  # let slow apps drain their input queue


def held_modifiers() -> list[int]:
    return [vk for vk in (VK_LCONTROL, VK_RCONTROL, VK_LSHIFT, VK_RSHIFT, VK_LMENU, VK_RMENU,
                          VK_LWIN, VK_RWIN) if api.GetAsyncKeyState(vk) & 0x8000]


def wait_for_modifiers_released(timeout: float) -> bool:
    """Wait until the user lets go of Ctrl/Shift/Alt/Win (they would alter Ctrl+V)."""
    deadline = time.monotonic() + timeout
    while held_modifiers():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.01)
    return True
