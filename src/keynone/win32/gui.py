"""Minimal Win32 windowing: Python-implemented windows, message loop, tray icon."""

from __future__ import annotations

import ctypes
import logging
import math
import struct
from collections.abc import Callable
from ctypes import wintypes
from pathlib import Path
from typing import ClassVar

from keynone.win32 import api

log = logging.getLogger(__name__)


def _dispatch(hwnd: int, msg: int, wparam: int, lparam: int) -> int:
    window = Window.registry.get(hwnd or 0)
    if window is not None:
        try:
            result = window.on_message(msg, wparam, lparam)
        except Exception:
            log.exception("window message %#x failed", msg)
            result = None
        if msg == api.WM_DESTROY:
            Window.registry.pop(hwnd or 0, None)
        if result is not None:
            return result
    return int(api.DefWindowProcW(hwnd, msg, wparam, lparam))


_WNDPROC = api.WNDPROC(_dispatch)  # module-level: must outlive every window


class Window:
    """Base class: subclasses override :meth:`on_message`."""

    registry: ClassVar[dict[int, Window]] = {}
    _registered: ClassVar[set[str]] = set()

    hwnd: int = 0

    def create(self, class_name: str, *, ex_style: int, style: int, rect: tuple[int, int, int, int],
               title: str = "") -> int:
        instance = api.GetModuleHandleW(None)
        if class_name not in Window._registered:
            wc = api.WNDCLASSEXW(cbSize=ctypes.sizeof(api.WNDCLASSEXW))
            wc.lpfnWndProc = _WNDPROC
            wc.hInstance = instance
            wc.lpszClassName = class_name
            if not api.RegisterClassExW(ctypes.byref(wc)):
                raise ctypes.WinError(ctypes.get_last_error())
            Window._registered.add(class_name)
        x, y, w, h = rect
        hwnd = api.CreateWindowExW(ex_style, class_name, title, style, x, y, w, h, None, None,
                                   instance, None)
        if not hwnd:
            raise ctypes.WinError(ctypes.get_last_error())
        self.hwnd = int(hwnd)
        Window.registry[self.hwnd] = self
        return self.hwnd

    def on_message(self, msg: int, wparam: int, lparam: int) -> int | None:
        return None

    def post(self, msg: int, wparam: int = 0, lparam: int = 0) -> None:
        if self.hwnd:
            api.PostMessageW(self.hwnd, msg, wparam, lparam)

    def destroy(self) -> None:
        if self.hwnd:
            api.DestroyWindow(self.hwnd)
            self.hwnd = 0


def run_message_loop() -> None:
    """Pump messages for the current thread until WM_QUIT."""
    msg = wintypes.MSG()
    while True:
        result = api.GetMessageW(ctypes.byref(msg), None, 0, 0)
        if result == 0 or result == -1:
            break
        api.TranslateMessage(ctypes.byref(msg))
        api.DispatchMessageW(ctypes.byref(msg))


# --------------------------------------------------------------------------- icons

BRAND_RGB = (59, 64, 87)  # the microphone badge: tray icon (idle) and settings window


def write_icon(path: Path, background: tuple[int, int, int], size: int = 32) -> Path:
    """Draw a round badge with a microphone glyph and save it as a .ico file."""

    def coverage(x: float, y: float) -> tuple[float, float]:
        cx = cy = size / 2
        r = size / 2 - 0.5
        badge = 1.0 if (x - cx) ** 2 + (y - cy) ** 2 <= r * r else 0.0
        s = size / 32
        # microphone capsule
        cap_top, cap_bottom, cap_half = 7 * s, 18 * s, 4 * s
        in_cap = abs(x - cx) <= cap_half and cap_top + cap_half <= y <= cap_bottom - cap_half
        in_cap |= (x - cx) ** 2 + (y - (cap_top + cap_half)) ** 2 <= cap_half**2
        in_cap |= (x - cx) ** 2 + (y - (cap_bottom - cap_half)) ** 2 <= cap_half**2
        # holder arc, stem and base
        d = math.hypot(x - cx, y - 15 * s)
        in_arc = 6.2 * s <= d <= 7.8 * s and y >= 15 * s
        in_stem = abs(x - cx) <= 0.9 * s and 22.5 * s <= y <= 25.5 * s
        in_base = abs(x - cx) <= 4 * s and 25 * s <= y <= 26.6 * s
        glyph = 1.0 if (in_cap or in_arc or in_stem or in_base) else 0.0
        return badge, glyph

    pixels = bytearray()
    steps = 4
    for row in range(size - 1, -1, -1):  # bottom-up DIB rows
        for col in range(size):
            badge_sum = glyph_sum = 0.0
            for sy in range(steps):
                for sx in range(steps):
                    b, g = coverage(col + (sx + 0.5) / steps, row + (sy + 0.5) / steps)
                    badge_sum += b
                    glyph_sum += g
            n = steps * steps
            a_badge, a_glyph = badge_sum / n, min(glyph_sum / n, badge_sum / n)
            r = background[0] * (1 - a_glyph) + 255 * a_glyph
            g = background[1] * (1 - a_glyph) + 255 * a_glyph
            b = background[2] * (1 - a_glyph) + 255 * a_glyph
            pixels += bytes((int(b), int(g), int(r), int(255 * a_badge)))
    mask = bytes(((size + 31) // 32) * 4 * size)
    header = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0, len(pixels) + len(mask),
                         0, 0, 0, 0)
    image = header + bytes(pixels) + mask
    icon = struct.pack("<HHH", 0, 1, 1) + struct.pack(
        "<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(image), 22
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(icon + image)
    return path


def load_icon(path: Path, size: int = 0) -> int:
    LR_LOADFROMFILE, IMAGE_ICON = 0x10, 1
    return int(api.LoadImageW(None, str(path), IMAGE_ICON, size, size, LR_LOADFROMFILE) or 0)


# --------------------------------------------------------------------------- tray icon

NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 0x1, 0x2, 0x4, 0x10
NIIF_INFO, NIIF_WARNING = 0x1, 0x2
MF_STRING, MF_SEPARATOR, MF_GRAYED = 0x0, 0x800, 0x1
TPM_RIGHTBUTTON, TPM_RETURNCMD, TPM_NONOTIFY = 0x2, 0x100, 0x80
WM_TRAY = api.WM_APP + 1
WM_NULL = 0x0000

MenuItem = tuple[str, Callable[[], None] | None]  # ("-", None) is a separator


class TrayIcon(Window):
    """Notification-area icon with a context menu (call from a message-loop thread)."""

    CLASS_NAME = "KeynoneTray"

    def __init__(self, tooltip: str, icon_path: Path, menu: Callable[[], list[MenuItem]],
                 on_close: Callable[[], None] | None = None,
                 handlers: dict[int, Callable[[int, int], None]] | None = None) -> None:
        self._tooltip = tooltip
        self._icon = load_icon(icon_path)
        self._menu = menu
        self._on_close = on_close
        self._handlers = handlers or {}  # private messages from other processes
        self._taskbar_created = api.RegisterWindowMessageW("TaskbarCreated")

    def show(self) -> None:
        self.create(self.CLASS_NAME, ex_style=0, style=0, rect=(0, 0, 0, 0))
        self._notify(NIM_ADD)

    def _data(self, flags: int) -> api.NOTIFYICONDATAW:
        data = api.NOTIFYICONDATAW(cbSize=ctypes.sizeof(api.NOTIFYICONDATAW))
        data.hWnd = self.hwnd
        data.uID = 1
        data.uFlags = flags
        data.uCallbackMessage = WM_TRAY
        data.hIcon = self._icon
        data.szTip = self._tooltip[:127]
        return data

    def _notify(self, action: int) -> None:
        api.Shell_NotifyIconW(action, ctypes.byref(self._data(NIF_MESSAGE | NIF_ICON | NIF_TIP)))

    def set_icon(self, icon_path: Path, tooltip: str | None = None) -> None:
        old, self._icon = self._icon, load_icon(icon_path)
        if tooltip is not None:
            self._tooltip = tooltip
        self._notify(NIM_MODIFY)
        if old:
            api.DestroyIcon(old)

    def set_tooltip(self, tooltip: str) -> None:
        self._tooltip = tooltip
        if self.hwnd:
            self._notify(NIM_MODIFY)

    def balloon(self, title: str, text: str, warning: bool = False) -> None:
        data = self._data(NIF_INFO)
        data.szInfoTitle = title[:63]
        data.szInfo = text[:255]
        data.dwInfoFlags = NIIF_WARNING if warning else NIIF_INFO
        api.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(data))

    def remove(self) -> None:
        if self.hwnd:
            api.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._data(0)))
            self.destroy()

    def _show_menu(self) -> None:
        items = self._menu()
        menu = api.CreatePopupMenu()
        try:
            for index, (label, action) in enumerate(items, start=1):
                if label == "-":
                    api.AppendMenuW(menu, MF_SEPARATOR, 0, None)
                else:
                    api.AppendMenuW(menu, MF_STRING | (MF_GRAYED if action is None else 0),
                                    index, label)
            point = wintypes.POINT()
            api.GetCursorPos(ctypes.byref(point))
            api.SetForegroundWindow(self.hwnd)  # required so the menu closes properly
            choice = api.TrackPopupMenu(menu, TPM_RIGHTBUTTON | TPM_RETURNCMD | TPM_NONOTIFY,
                                        point.x, point.y, 0, self.hwnd, None)
            api.PostMessageW(self.hwnd, WM_NULL, 0, 0)
        finally:
            api.DestroyMenu(menu)
        if choice:
            action = items[choice - 1][1]
            if action:
                action()

    def on_message(self, msg: int, wparam: int, lparam: int) -> int | None:
        if msg == WM_TRAY:
            if lparam in (api.WM_RBUTTONUP, api.WM_LBUTTONUP, api.WM_CONTEXTMENU):
                self._show_menu()
            return 0
        if msg == self._taskbar_created:  # Explorer restarted: re-add the icon
            self._notify(NIM_ADD)
            return 0
        if msg == api.WM_CLOSE:  # `keynone stop` or system shutdown
            if self._on_close:
                self._on_close()
            return 0
        handler = self._handlers.get(msg)
        if handler is not None:
            handler(wparam, lparam)
            return 0
        return None
