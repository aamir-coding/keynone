"""Opt-in desktop test: insert text into a real Win32 text box we create ourselves.

Run with:  $env:KEYNONE_DESKTOP_TESTS = "1"; pytest tests/test_desktop_inject.py

Safety: the test only sends keystrokes after confirming that its own window
holds the keyboard focus, so it can never type into another application. It
also checks that the user's clipboard is restored afterwards.
"""

from __future__ import annotations

import ctypes
import os
import threading
import time
from collections.abc import Iterator

import pytest

from keynone.config import AppProfile, InsertConfig
from keynone.engine.inject import Inserter
from keynone.win32 import api, clipboard
from keynone.win32.windows import window_info

pytestmark = [
    pytest.mark.desktop,
    pytest.mark.skipif(os.environ.get("KEYNONE_DESKTOP_TESTS") != "1",
                       reason="set KEYNONE_DESKTOP_TESTS=1 to run desktop tests"),
]

WS_OVERLAPPEDWINDOW, WS_VISIBLE, WS_CHILD, WS_BORDER = 0x00CF0000, 0x10000000, 0x40000000, 0x800000
ES_MULTILINE, ES_AUTOVSCROLL = 0x0004, 0x0040
WM_SETTEXT, WM_GETTEXT, WM_GETTEXTLENGTH = 0x000C, 0x000D, 0x000E


class _TextBoxWindow:
    """A top-level window with a multi-line EDIT control, on its own UI thread."""

    def __init__(self) -> None:
        self.top = 0
        self.edit = 0
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.wait(5)

    def _run(self) -> None:
        instance = api.GetModuleHandleW(None)
        self.top = api.CreateWindowExW(0, "STATIC", "Keynone injection test",
                                       WS_OVERLAPPEDWINDOW | WS_VISIBLE, 200, 200, 520, 260,
                                       None, None, instance, None)
        self.edit = api.CreateWindowExW(
            0, "EDIT", "", WS_CHILD | WS_VISIBLE | WS_BORDER | ES_MULTILINE | ES_AUTOVSCROLL,
            10, 10, 480, 180, self.top, None, instance, None,
        )
        api.SetForegroundWindow(self.top)
        api.SetFocus(self.edit)
        self._ready.set()
        from keynone.win32.gui import run_message_loop

        run_message_loop()

    def text(self) -> str:
        length = api.SendMessageW(self.edit, WM_GETTEXTLENGTH, 0, 0)
        buffer = ctypes.create_unicode_buffer(length + 1)
        api.SendMessageW(self.edit, WM_GETTEXT, length + 1, ctypes.addressof(buffer))
        return buffer.value.replace("\r\n", "\n")

    def clear(self) -> None:
        empty = ctypes.create_unicode_buffer("")
        api.SendMessageW(self.edit, WM_SETTEXT, 0, ctypes.addressof(empty))

    def close(self) -> None:
        api.PostMessageW(self.top, api.WM_CLOSE, 0, 0)
        api.DestroyWindow(self.top)


@pytest.fixture
def textbox() -> Iterator[_TextBoxWindow]:
    window = _TextBoxWindow()
    time.sleep(0.3)
    if api.GetForegroundWindow() != window.top:
        window.close()
        pytest.skip("Windows did not give the test window the keyboard focus")
    yield window
    window.close()


def _focused(window: _TextBoxWindow) -> bool:
    return api.GetForegroundWindow() == window.top


def test_paste_inserts_text_and_restores_clipboard(textbox: _TextBoxWindow) -> None:
    before = clipboard.get_text()
    text = "Hello from Keynone, déjà vu ✓\nSecond line 4:30 PM "
    inserter = Inserter(InsertConfig(method="paste", restore_delay_ms=400))
    assert _focused(textbox), "focus moved away; aborting before sending keys"
    result = inserter.insert(text, AppProfile(), window_info(textbox.top))
    time.sleep(0.4)
    assert result.inserted, result.message
    assert textbox.text() == text
    assert clipboard.get_text() == before  # the user's clipboard came back


def test_typing_mode_inserts_unicode(textbox: _TextBoxWindow) -> None:
    textbox.clear()
    text = "Typed: naïve café ✓ 😀"
    inserter = Inserter(InsertConfig(method="type"))
    assert _focused(textbox), "focus moved away; aborting before sending keys"
    result = inserter.insert(text, AppProfile(method="type"), window_info(textbox.top))
    time.sleep(0.4)
    assert result.inserted, result.message
    assert textbox.text() == text
