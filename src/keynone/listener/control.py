"""Reaching a running listener from other processes (command line, settings window).

The listener's hidden tray window is its address: these helpers find it by its
class name and post it a message. Nothing else is shared, so they cost nothing
when the listener isn't running.
"""

from __future__ import annotations

import time

from keynone.win32 import api
from keynone.win32.gui import TrayIcon

TRAY_CLASS = TrayIcon.CLASS_NAME
WM_RELOAD_CONFIG = api.WM_APP + 2  # config.toml changed: apply it when idle
WM_SUSPEND_HOTKEYS = api.WM_APP + 3  # wParam = seconds to ignore the hotkeys (0 = resume)
MAX_SUSPEND_SECONDS = 60


def listener_window() -> int:
    return int(api.FindWindowW(TRAY_CLASS, None) or 0)


def is_running() -> bool:
    return bool(listener_window())


def _post(message: int, wparam: int = 0) -> bool:
    hwnd = listener_window()
    return bool(hwnd) and bool(api.PostMessageW(hwnd, message, wparam, 0))


def request_reload() -> bool:
    """Tell the listener that config.toml changed (it also notices by itself within ~1 s)."""
    return _post(WM_RELOAD_CONFIG)


def suspend_hotkeys(seconds: int) -> bool:
    """Ignore the hotkeys for a while, e.g. while the settings window records a new one."""
    return _post(WM_SUSPEND_HOTKEYS, max(0, min(int(seconds), MAX_SUSPEND_SECONDS)))


def resume_hotkeys() -> bool:
    return _post(WM_SUSPEND_HOTKEYS, 0)


def stop_listener(timeout: float = 5.0) -> bool:
    """Ask a running listener to exit cleanly; False if none was running."""
    hwnd = listener_window()
    if not hwnd:
        return False
    api.PostMessageW(hwnd, api.WM_CLOSE, 0, 0)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and listener_window():
        time.sleep(0.05)
    return True
