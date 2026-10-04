"""Low-level keyboard hook (WH_KEYBOARD_LL).

The hook is event-driven: Windows calls it only when a key is pressed, so an
idle listener uses no CPU. Keys are inspected in memory and never stored.
The installing thread must run a message loop, and the callback must return
quickly (Windows silently removes slow hooks), so it only updates a small
state machine and hands real work to another thread.
"""

from __future__ import annotations

import ctypes
import logging
from collections.abc import Callable

from keynone.win32 import api
from keynone.win32.keys import INJECTED_TAG

log = logging.getLogger(__name__)

WH_KEYBOARD_LL = 13
HC_ACTION = 0

# handler(vk, is_down) -> True to swallow the key
KeyHandler = Callable[[int, bool], bool]


class KeyboardHook:
    def __init__(self, handler: KeyHandler) -> None:
        self._handler = handler
        self._proc = api.HOOKPROC(self._callback)  # keep a reference: called from C
        self._hook: int | None = None

    def install(self) -> None:
        hook = api.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, api.GetModuleHandleW(None), 0)
        if not hook:
            raise ctypes.WinError(ctypes.get_last_error())
        self._hook = hook

    def uninstall(self) -> None:
        if self._hook:
            api.UnhookWindowsHookEx(self._hook)
            self._hook = None

    def _callback(self, code: int, wparam: int, lparam: int) -> int:
        if code == HC_ACTION:
            info = api.KBDLLHOOKSTRUCT.from_address(lparam)
            if info.dwExtraInfo != INJECTED_TAG:
                down = wparam in (api.WM_KEYDOWN, api.WM_SYSKEYDOWN)
                try:
                    if self._handler(int(info.vkCode), down):
                        return 1
                except Exception:  # never let an exception escape into Windows
                    log.exception("keyboard hook handler failed")
        return int(api.CallNextHookEx(None, code, wparam, lparam))
