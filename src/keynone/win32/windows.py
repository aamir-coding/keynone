"""Information about the window the user is typing into."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass

from keynone.win32 import api
from keynone.win32.process import process_image_name


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    pid: int
    exe: str
    title: str
    class_name: str
    focus_class: str
    password_field: bool

    @property
    def is_valid(self) -> bool:
        return bool(self.hwnd)


def _text(hwnd: int) -> str:
    length = api.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buffer = ctypes.create_unicode_buffer(length + 1)
    api.GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value


def _class(hwnd: int) -> str:
    if not hwnd:
        return ""
    buffer = ctypes.create_unicode_buffer(256)
    api.GetClassNameW(hwnd, buffer, 256)
    return buffer.value


def window_info(hwnd: int | None = None) -> WindowInfo:
    """Describe ``hwnd`` (default: the foreground window)."""
    hwnd = hwnd or api.GetForegroundWindow() or 0
    if not hwnd:
        return WindowInfo(0, 0, "", "", "", "", False)
    pid = wintypes.DWORD()
    thread_id = api.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    info = api.GUITHREADINFO(cbSize=ctypes.sizeof(api.GUITHREADINFO))
    focus = 0
    if thread_id and api.GetGUIThreadInfo(thread_id, ctypes.byref(info)):
        focus = info.hwndFocus or 0
    focus_class = _class(focus)
    password = False
    if focus and (focus_class.lower() == "edit" or focus_class.lower().startswith("richedit")):
        password = bool(api.GetWindowLongPtrW(focus, api.GWL_STYLE) & api.ES_PASSWORD)
    return WindowInfo(
        hwnd=int(hwnd),
        pid=pid.value,
        exe=process_image_name(pid.value),
        title=_text(hwnd),
        class_name=_class(hwnd),
        focus_class=focus_class,
        password_field=password,
    )


def work_area(hwnd: int | None) -> tuple[tuple[int, int, int, int], float]:
    """Work area (left, top, right, bottom) of the monitor showing ``hwnd`` and its scale."""
    if hwnd:
        monitor = api.MonitorFromWindow(hwnd, api.MONITOR_DEFAULTTONEAREST)
    else:
        point = wintypes.POINT()
        api.GetCursorPos(ctypes.byref(point))
        monitor = api.MonitorFromPoint(point, api.MONITOR_DEFAULTTONEAREST)
    info = api.MONITORINFO(cbSize=ctypes.sizeof(api.MONITORINFO))
    api.GetMonitorInfoW(monitor, ctypes.byref(info))
    dpi_x, dpi_y = wintypes.UINT(96), wintypes.UINT(96)
    try:
        api.GetDpiForMonitor(monitor, 0, ctypes.byref(dpi_x), ctypes.byref(dpi_y))
    except OSError:
        pass
    rect = info.rcWork
    return (rect.left, rect.top, rect.right, rect.bottom), dpi_x.value / 96.0
