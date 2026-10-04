"""Live transcript overlay: a small pill near the bottom of the active monitor.

It is click-through, never takes focus (WS_EX_NOACTIVATE) and is drawn with
double-buffered GDI on its own UI thread; other threads only post updates.
"""

from __future__ import annotations

import ctypes
import logging
import threading
from ctypes import wintypes
from dataclasses import dataclass

from keynone.win32 import api
from keynone.win32.gui import Window, run_message_loop
from keynone.win32.windows import work_area

log = logging.getLogger(__name__)

WM_OVERLAY_UPDATE = api.WM_APP + 10
WM_OVERLAY_SHOW = api.WM_APP + 11
WM_OVERLAY_HIDE = api.WM_APP + 12
_HIDE_TIMER = 1

_BG = api.rgb(30, 31, 36)
_TEXT = api.rgb(242, 243, 245)
_MUTED = api.rgb(150, 154, 166)
_TONES = {
    "listening": api.rgb(255, 90, 95),
    "working": api.rgb(255, 176, 32),
    "done": api.rgb(61, 214, 140),
    "error": api.rgb(255, 90, 95),
    "idle": api.rgb(120, 124, 136),
}


@dataclass
class _State:
    status: str = ""
    text: str = ""
    tone: str = "listening"
    level: float = 0.0


class Overlay(Window):
    def __init__(self, *, opacity: float = 0.94, position: str = "bottom",
                 enabled: bool = True) -> None:
        self.enabled = enabled
        self._alpha = max(50, min(255, int(opacity * 255)))
        self._position = position
        self._state = _State()
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._update_pending = False
        self._scale = 1.0
        self._size = (0, 0)
        self._fonts: dict[str, int] = {}

    # ----------------------------------------------------------------- threading

    def start(self) -> None:
        if not self.enabled:
            return
        self._thread = threading.Thread(target=self._ui_thread, name="overlay", daemon=True)
        self._thread.start()
        self._ready.wait(5)

    def _ui_thread(self) -> None:
        try:
            ex = (api.WS_EX_TOPMOST | api.WS_EX_TOOLWINDOW | api.WS_EX_NOACTIVATE
                  | api.WS_EX_LAYERED | api.WS_EX_TRANSPARENT)
            self.create("KeynoneOverlay", ex_style=ex, style=api.WS_POPUP, rect=(0, 0, 10, 10))
            api.SetLayeredWindowAttributes(self.hwnd, 0, self._alpha, api.LWA_ALPHA)
        except OSError:
            log.exception("overlay window could not be created")
            self.enabled = False
            self._ready.set()
            return
        self._ready.set()
        run_message_loop()
        for font in self._fonts.values():
            api.DeleteObject(font)

    def close(self) -> None:
        if self.enabled and self.hwnd:
            self.post(api.WM_CLOSE)
            if self._thread:
                self._thread.join(timeout=2)

    # ----------------------------------------------------------------- public API (any thread)

    def show(self, anchor_hwnd: int = 0) -> None:
        if self.enabled:
            self.post(WM_OVERLAY_SHOW, anchor_hwnd)

    def hide(self, after_ms: int = 0) -> None:
        if self.enabled:
            self.post(WM_OVERLAY_HIDE, after_ms)

    def update(self, *, status: str | None = None, text: str | None = None,
               tone: str | None = None, level: float | None = None) -> None:
        if not self.enabled:
            return
        with self._lock:
            if status is not None:
                self._state.status = status
            if text is not None:
                self._state.text = text
            if tone is not None:
                self._state.tone = tone
            if level is not None:
                self._state.level = max(level, self._state.level * 0.8)
            if self._update_pending:
                return
            self._update_pending = True
        self.post(WM_OVERLAY_UPDATE)

    # ----------------------------------------------------------------- UI thread

    def _font(self, name: str, height: int, weight: int) -> int:
        key = f"{name}-{height}-{weight}"
        if key not in self._fonts:
            self._fonts[key] = api.CreateFontW(-height, 0, 0, 0, weight, 0, 0, 0, 1, 0, 0,
                                               api.CLEARTYPE_QUALITY, 0, "Segoe UI")
        return self._fonts[key]

    def _place(self, anchor_hwnd: int) -> None:
        (left, top, right, bottom), scale = work_area(anchor_hwnd)
        self._scale = scale
        width = min(int(640 * scale), right - left - int(32 * scale))
        height = int(76 * scale)
        x = left + (right - left - width) // 2
        margin = int(56 * scale)
        y = bottom - height - margin if self._position == "bottom" else top + margin
        self._size = (width, height)
        api.SetWindowPos(self.hwnd, api.HWND_TOPMOST, x, y, width, height,
                         api.SWP_NOACTIVATE | api.SWP_SHOWWINDOW | api.SWP_NOOWNERZORDER)
        radius = int(18 * scale)
        api.SetWindowRgn(self.hwnd, api.CreateRoundRectRgn(0, 0, width + 1, height + 1, radius,
                                                           radius), True)

    def on_message(self, msg: int, wparam: int, lparam: int) -> int | None:
        if msg == WM_OVERLAY_UPDATE:
            with self._lock:
                self._update_pending = False
            api.InvalidateRect(self.hwnd, None, False)
            return 0
        if msg == WM_OVERLAY_SHOW:
            api.KillTimer(self.hwnd, _HIDE_TIMER)
            self._place(wparam)
            api.ShowWindow(self.hwnd, api.SW_SHOWNOACTIVATE)
            api.InvalidateRect(self.hwnd, None, False)
            return 0
        if msg == WM_OVERLAY_HIDE:
            if wparam:
                api.SetTimer(self.hwnd, _HIDE_TIMER, wparam, None)
            else:
                api.ShowWindow(self.hwnd, api.SW_HIDE)
            return 0
        if msg == api.WM_TIMER and wparam == _HIDE_TIMER:
            api.KillTimer(self.hwnd, _HIDE_TIMER)
            api.ShowWindow(self.hwnd, api.SW_HIDE)
            return 0
        if msg == api.WM_MOUSEACTIVATE:
            return api.MA_NOACTIVATE
        if msg == api.WM_ERASEBKGND:
            return 1
        if msg == api.WM_PAINT:
            self._paint()
            return 0
        if msg == api.WM_CLOSE:
            api.DestroyWindow(self.hwnd)
            return 0
        if msg == api.WM_DESTROY:
            api.PostQuitMessage(0)
            return 0
        return None

    def _fit_text(self, dc: int, text: str, rect: wintypes.RECT, max_height: int) -> str:
        """Longest tail of ``text`` that fits in ``max_height`` (prefixed by an ellipsis)."""
        flags = api.DT_WORDBREAK | api.DT_NOPREFIX | api.DT_CALCRECT

        def fits(candidate: str) -> bool:
            probe = wintypes.RECT(rect.left, rect.top, rect.right, rect.top)
            api.DrawTextW(dc, candidate, -1, ctypes.byref(probe), flags)
            return probe.bottom - probe.top <= max_height

        text = " ".join(text.split())
        if fits(text):
            return text
        lo, hi = 0, len(text)
        while lo < hi:
            mid = (lo + hi) // 2
            if fits("…" + text[mid:].lstrip()):
                hi = mid
            else:
                lo = mid + 1
        return "…" + text[lo:].lstrip()

    def _paint(self) -> None:
        ps = api.PAINTSTRUCT()
        hdc = api.BeginPaint(self.hwnd, ctypes.byref(ps))
        try:
            width, height = self._size
            if not width:
                return
            with self._lock:
                state = _State(**vars(self._state))
            s = self._scale
            mem = api.CreateCompatibleDC(hdc)
            bitmap = api.CreateCompatibleBitmap(hdc, width, height)
            old_bitmap = api.SelectObject(mem, bitmap)
            try:
                self._draw(mem, width, height, s, state)
                api.BitBlt(hdc, 0, 0, width, height, mem, 0, 0, api.SRCCOPY)
            finally:
                api.SelectObject(mem, old_bitmap)
                api.DeleteObject(bitmap)
                api.DeleteDC(mem)
        finally:
            api.EndPaint(self.hwnd, ctypes.byref(ps))

    def _draw(self, dc: int, width: int, height: int, s: float, state: _State) -> None:
        background = api.CreateSolidBrush(_BG)
        api.FillRect(dc, ctypes.byref(wintypes.RECT(0, 0, width, height)), background)
        api.DeleteObject(background)
        api.SetBkMode(dc, api.TRANSPARENT)

        # level meter / status dot
        tone = api.CreateSolidBrush(_TONES.get(state.tone, _TONES["idle"]))
        cx, cy = int(28 * s), height // 2
        bar_w, gap = max(2, int(3 * s)), max(2, int(3 * s))
        bars = 4 if state.tone == "listening" else 1
        for i in range(bars):
            if state.tone == "listening":
                shape = (0.55, 1.0, 0.8, 0.45)[i]
                h = max(int(4 * s), int((6 + 22 * min(1.0, state.level * 4) * shape) * s))
            else:
                h = int(10 * s)
            x = cx - (bars * (bar_w + gap)) // 2 + i * (bar_w + gap)
            if state.tone != "listening":
                x, bar_w = cx - int(5 * s), int(10 * s)
            api.FillRect(dc, ctypes.byref(wintypes.RECT(x, cy - h // 2, x + bar_w, cy + h // 2)),
                         tone)
        api.DeleteObject(tone)

        left, right = int(52 * s), width - int(18 * s)
        # status line
        api.SelectObject(dc, self._font("status", int(12 * s), api.FW_SEMIBOLD))
        api.SetTextColor(dc, _MUTED)
        status_rect = wintypes.RECT(left, int(9 * s), right, int(27 * s))
        api.DrawTextW(dc, state.status, -1, ctypes.byref(status_rect),
                      api.DT_SINGLELINE | api.DT_NOPREFIX | api.DT_END_ELLIPSIS)
        # transcript (two lines, newest text wins)
        api.SelectObject(dc, self._font("text", int(15 * s), api.FW_NORMAL))
        api.SetTextColor(dc, _TEXT)
        text_rect = wintypes.RECT(left, int(28 * s), right, height - int(8 * s))
        line_height = int(15 * s * 1.35)
        shown = self._fit_text(dc, state.text, text_rect, line_height * 2)
        api.DrawTextW(dc, shown or " ", -1, ctypes.byref(text_rect),
                      api.DT_WORDBREAK | api.DT_NOPREFIX)
