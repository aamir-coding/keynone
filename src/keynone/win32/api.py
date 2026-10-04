"""ctypes declarations for the Win32 APIs used by Keynone.

Every function gets explicit ``argtypes``/``restype``: without them ctypes
truncates 64-bit handles/pointers to 32-bit ints, which corrupts memory at
random. All declarations live here so no two modules configure the same
function differently.
"""

from __future__ import annotations

import contextlib
import ctypes
from ctypes import wintypes
from typing import Any

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
winmm = ctypes.WinDLL("winmm", use_last_error=True)
shcore = ctypes.WinDLL("shcore", use_last_error=True)

LRESULT = ctypes.c_ssize_t
LONG_PTR = ctypes.c_ssize_t
ULONG_PTR = ctypes.c_size_t
UINT_PTR = ctypes.c_size_t
HRESULT = ctypes.c_long

WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                             wintypes.LPARAM)
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

# --------------------------------------------------------------------------- constants

WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_PAINT = 0x000F
WM_ERASEBKGND = 0x0014
WM_TIMER = 0x0113
WM_COMMAND = 0x0111
WM_QUIT = 0x0012
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_LBUTTONUP = 0x0202
WM_RBUTTONUP = 0x0205
WM_LBUTTONDBLCLK = 0x0203
WM_CONTEXTMENU = 0x007B
WM_APP = 0x8000
WM_MOUSEACTIVATE = 0x0021
MA_NOACTIVATE = 3

WS_POPUP = 0x80000000
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_NOACTIVATE = 0x08000000

SW_HIDE = 0
SW_SHOWNOACTIVATE = 4
HWND_TOPMOST = wintypes.HWND(-1)
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
SWP_NOOWNERZORDER = 0x0200
LWA_ALPHA = 0x00000002
MONITOR_DEFAULTTONEAREST = 2
DT_LEFT = 0x0000
DT_VCENTER = 0x0004
DT_WORDBREAK = 0x0010
DT_SINGLELINE = 0x0020
DT_NOPREFIX = 0x0800
DT_CALCRECT = 0x0400
DT_END_ELLIPSIS = 0x8000
TRANSPARENT = 1
SRCCOPY = 0x00CC0020
FW_NORMAL = 400
FW_SEMIBOLD = 600
CLEARTYPE_QUALITY = 5
GWL_STYLE = -16
ES_PASSWORD = 0x0020
GUI_CARETBLINKING = 0x1
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = wintypes.HANDLE(-4)
ERROR_ALREADY_EXISTS = 183

# --------------------------------------------------------------------------- structures


class PAINTSTRUCT(ctypes.Structure):
    _fields_ = [
        ("hdc", wintypes.HDC),
        ("fErase", wintypes.BOOL),
        ("rcPaint", wintypes.RECT),
        ("fRestore", wintypes.BOOL),
        ("fIncUpdate", wintypes.BOOL),
        ("rgbReserved", wintypes.BYTE * 32),
    ]


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
        ("hIconSm", wintypes.HICON),
    ]


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("hwndActive", wintypes.HWND),
        ("hwndFocus", wintypes.HWND),
        ("hwndCapture", wintypes.HWND),
        ("hwndMenuOwner", wintypes.HWND),
        ("hwndMoveSize", wintypes.HWND),
        ("hwndCaret", wintypes.HWND),
        ("rcCaret", wintypes.RECT),
    ]


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", wintypes.BYTE * 8),
    ]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", GUID),
        ("hBalloonIcon", wintypes.HICON),
    ]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


class WAVEFORMATEX(ctypes.Structure):
    _pack_ = 1  # matches <mmreg.h> (sizeof == 18)
    _fields_ = [
        ("wFormatTag", wintypes.WORD),
        ("nChannels", wintypes.WORD),
        ("nSamplesPerSec", wintypes.DWORD),
        ("nAvgBytesPerSec", wintypes.DWORD),
        ("nBlockAlign", wintypes.WORD),
        ("wBitsPerSample", wintypes.WORD),
        ("cbSize", wintypes.WORD),
    ]


class WAVEHDR(ctypes.Structure):
    pass


WAVEHDR._fields_ = [
    ("lpData", ctypes.c_void_p),
    ("dwBufferLength", wintypes.DWORD),
    ("dwBytesRecorded", wintypes.DWORD),
    ("dwUser", ULONG_PTR),
    ("dwFlags", wintypes.DWORD),
    ("dwLoops", wintypes.DWORD),
    ("lpNext", ctypes.POINTER(WAVEHDR)),
    ("reserved", ULONG_PTR),
]


class WAVEINCAPSW(ctypes.Structure):
    _fields_ = [
        ("wMid", wintypes.WORD),
        ("wPid", wintypes.WORD),
        ("vDriverVersion", wintypes.UINT),
        ("szPname", wintypes.WCHAR * 32),
        ("dwFormats", wintypes.DWORD),
        ("wChannels", wintypes.WORD),
        ("wReserved1", wintypes.WORD),
    ]


# --------------------------------------------------------------------------- functions


def _fn(dll: ctypes.WinDLL, name: str, restype: object, *argtypes: object) -> Any:
    func = getattr(dll, name)
    func.restype = restype
    func.argtypes = list(argtypes)
    return func


H, U, I, D = wintypes.HWND, wintypes.UINT, ctypes.c_int, wintypes.DWORD
W, L = wintypes.WPARAM, wintypes.LPARAM

# user32: windows & messages
GetForegroundWindow = _fn(user32, "GetForegroundWindow", H)
GetWindowThreadProcessId = _fn(user32, "GetWindowThreadProcessId", D, H, ctypes.POINTER(D))
GetWindowTextLengthW = _fn(user32, "GetWindowTextLengthW", I, H)
GetWindowTextW = _fn(user32, "GetWindowTextW", I, H, wintypes.LPWSTR, I)
GetClassNameW = _fn(user32, "GetClassNameW", I, H, wintypes.LPWSTR, I)
GetGUIThreadInfo = _fn(user32, "GetGUIThreadInfo", wintypes.BOOL, D,
                       ctypes.POINTER(GUITHREADINFO))
GetWindowLongPtrW = _fn(user32, "GetWindowLongPtrW", LONG_PTR, H, I)
IsWindow = _fn(user32, "IsWindow", wintypes.BOOL, H)
FindWindowW = _fn(user32, "FindWindowW", H, wintypes.LPCWSTR, wintypes.LPCWSTR)
GetMessageW = _fn(user32, "GetMessageW", wintypes.BOOL, ctypes.POINTER(wintypes.MSG), H, U, U)
TranslateMessage = _fn(user32, "TranslateMessage", wintypes.BOOL, ctypes.POINTER(wintypes.MSG))
DispatchMessageW = _fn(user32, "DispatchMessageW", LRESULT, ctypes.POINTER(wintypes.MSG))
PostMessageW = _fn(user32, "PostMessageW", wintypes.BOOL, H, U, W, L)
SendMessageW = _fn(user32, "SendMessageW", LRESULT, H, U, W, L)
SetFocus = _fn(user32, "SetFocus", H, H)
PostThreadMessageW = _fn(user32, "PostThreadMessageW", wintypes.BOOL, D, U, W, L)
PostQuitMessage = _fn(user32, "PostQuitMessage", None, I)
DefWindowProcW = _fn(user32, "DefWindowProcW", LRESULT, H, U, W, L)
RegisterClassExW = _fn(user32, "RegisterClassExW", wintypes.ATOM, ctypes.POINTER(WNDCLASSEXW))
CreateWindowExW = _fn(user32, "CreateWindowExW", H, D, wintypes.LPCWSTR, wintypes.LPCWSTR, D,
                      I, I, I, I, H, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID)
DestroyWindow = _fn(user32, "DestroyWindow", wintypes.BOOL, H)
ShowWindow = _fn(user32, "ShowWindow", wintypes.BOOL, H, I)
SetWindowPos = _fn(user32, "SetWindowPos", wintypes.BOOL, H, H, I, I, I, I, U)
InvalidateRect = _fn(user32, "InvalidateRect", wintypes.BOOL, H, ctypes.POINTER(wintypes.RECT),
                     wintypes.BOOL)
BeginPaint = _fn(user32, "BeginPaint", wintypes.HDC, H, ctypes.POINTER(PAINTSTRUCT))
EndPaint = _fn(user32, "EndPaint", wintypes.BOOL, H, ctypes.POINTER(PAINTSTRUCT))
GetClientRect = _fn(user32, "GetClientRect", wintypes.BOOL, H, ctypes.POINTER(wintypes.RECT))
FillRect = _fn(user32, "FillRect", I, wintypes.HDC, ctypes.POINTER(wintypes.RECT),
               wintypes.HBRUSH)
DrawTextW = _fn(user32, "DrawTextW", I, wintypes.HDC, wintypes.LPCWSTR, I,
                ctypes.POINTER(wintypes.RECT), U)
SetLayeredWindowAttributes = _fn(user32, "SetLayeredWindowAttributes", wintypes.BOOL, H,
                                 wintypes.COLORREF, wintypes.BYTE, D)
SetWindowRgn = _fn(user32, "SetWindowRgn", I, H, wintypes.HRGN, wintypes.BOOL)
MonitorFromWindow = _fn(user32, "MonitorFromWindow", wintypes.HMONITOR, H, D)
MonitorFromPoint = _fn(user32, "MonitorFromPoint", wintypes.HMONITOR, wintypes.POINT, D)
GetMonitorInfoW = _fn(user32, "GetMonitorInfoW", wintypes.BOOL, wintypes.HMONITOR,
                      ctypes.POINTER(MONITORINFO))
SetProcessDpiAwarenessContext = _fn(user32, "SetProcessDpiAwarenessContext", wintypes.BOOL,
                                    wintypes.HANDLE)
LoadImageW = _fn(user32, "LoadImageW", wintypes.HANDLE, wintypes.HINSTANCE, wintypes.LPCWSTR, U,
                 I, I, U)
DestroyIcon = _fn(user32, "DestroyIcon", wintypes.BOOL, wintypes.HICON)
CreatePopupMenu = _fn(user32, "CreatePopupMenu", wintypes.HMENU)
AppendMenuW = _fn(user32, "AppendMenuW", wintypes.BOOL, wintypes.HMENU, U, UINT_PTR,
                  wintypes.LPCWSTR)
TrackPopupMenu = _fn(user32, "TrackPopupMenu", wintypes.BOOL, wintypes.HMENU, U, I, I, I, H,
                     ctypes.POINTER(wintypes.RECT))
DestroyMenu = _fn(user32, "DestroyMenu", wintypes.BOOL, wintypes.HMENU)
GetCursorPos = _fn(user32, "GetCursorPos", wintypes.BOOL, ctypes.POINTER(wintypes.POINT))
SetForegroundWindow = _fn(user32, "SetForegroundWindow", wintypes.BOOL, H)
RegisterWindowMessageW = _fn(user32, "RegisterWindowMessageW", U, wintypes.LPCWSTR)
SetTimer = _fn(user32, "SetTimer", UINT_PTR, H, UINT_PTR, U, wintypes.LPVOID)
KillTimer = _fn(user32, "KillTimer", wintypes.BOOL, H, UINT_PTR)

# user32: input
GetAsyncKeyState = _fn(user32, "GetAsyncKeyState", wintypes.SHORT, I)
SendInput = _fn(user32, "SendInput", U, U, ctypes.POINTER(INPUT), I)
MapVirtualKeyW = _fn(user32, "MapVirtualKeyW", U, U, U)
SetWindowsHookExW = _fn(user32, "SetWindowsHookExW", wintypes.HHOOK, I, HOOKPROC,
                        wintypes.HINSTANCE, D)
UnhookWindowsHookEx = _fn(user32, "UnhookWindowsHookEx", wintypes.BOOL, wintypes.HHOOK)
CallNextHookEx = _fn(user32, "CallNextHookEx", LRESULT, wintypes.HHOOK, I, W, L)

# user32: clipboard
OpenClipboard = _fn(user32, "OpenClipboard", wintypes.BOOL, H)
CloseClipboard = _fn(user32, "CloseClipboard", wintypes.BOOL)
EmptyClipboard = _fn(user32, "EmptyClipboard", wintypes.BOOL)
GetClipboardData = _fn(user32, "GetClipboardData", wintypes.HANDLE, U)
SetClipboardData = _fn(user32, "SetClipboardData", wintypes.HANDLE, U, wintypes.HANDLE)
EnumClipboardFormats = _fn(user32, "EnumClipboardFormats", U, U)
RegisterClipboardFormatW = _fn(user32, "RegisterClipboardFormatW", U, wintypes.LPCWSTR)
GetClipboardSequenceNumber = _fn(user32, "GetClipboardSequenceNumber", D)

# kernel32
GetModuleHandleW = _fn(kernel32, "GetModuleHandleW", wintypes.HMODULE, wintypes.LPCWSTR)
GetCurrentThreadId = _fn(kernel32, "GetCurrentThreadId", D)
GlobalAlloc = _fn(kernel32, "GlobalAlloc", wintypes.HGLOBAL, U, ctypes.c_size_t)
GlobalLock = _fn(kernel32, "GlobalLock", wintypes.LPVOID, wintypes.HGLOBAL)
GlobalUnlock = _fn(kernel32, "GlobalUnlock", wintypes.BOOL, wintypes.HGLOBAL)
GlobalSize = _fn(kernel32, "GlobalSize", ctypes.c_size_t, wintypes.HGLOBAL)
GlobalFree = _fn(kernel32, "GlobalFree", wintypes.HGLOBAL, wintypes.HGLOBAL)
CreateEventW = _fn(kernel32, "CreateEventW", wintypes.HANDLE, wintypes.LPVOID, wintypes.BOOL,
                   wintypes.BOOL, wintypes.LPCWSTR)
WaitForSingleObject = _fn(kernel32, "WaitForSingleObject", D, wintypes.HANDLE, D)
CloseHandle = _fn(kernel32, "CloseHandle", wintypes.BOOL, wintypes.HANDLE)
CreateMutexW = _fn(kernel32, "CreateMutexW", wintypes.HANDLE, wintypes.LPVOID, wintypes.BOOL,
                   wintypes.LPCWSTR)

# gdi32
CreateSolidBrush = _fn(gdi32, "CreateSolidBrush", wintypes.HBRUSH, wintypes.COLORREF)
DeleteObject = _fn(gdi32, "DeleteObject", wintypes.BOOL, wintypes.HGDIOBJ)
SelectObject = _fn(gdi32, "SelectObject", wintypes.HGDIOBJ, wintypes.HDC, wintypes.HGDIOBJ)
CreateFontW = _fn(gdi32, "CreateFontW", wintypes.HFONT, I, I, I, I, I, D, D, D, D, D, D, D, D,
                  wintypes.LPCWSTR)
SetTextColor = _fn(gdi32, "SetTextColor", wintypes.COLORREF, wintypes.HDC, wintypes.COLORREF)
SetBkMode = _fn(gdi32, "SetBkMode", I, wintypes.HDC, I)
CreateCompatibleDC = _fn(gdi32, "CreateCompatibleDC", wintypes.HDC, wintypes.HDC)
CreateCompatibleBitmap = _fn(gdi32, "CreateCompatibleBitmap", wintypes.HBITMAP, wintypes.HDC,
                             I, I)
BitBlt = _fn(gdi32, "BitBlt", wintypes.BOOL, wintypes.HDC, I, I, I, I, wintypes.HDC, I, I, D)
DeleteDC = _fn(gdi32, "DeleteDC", wintypes.BOOL, wintypes.HDC)
CreateRoundRectRgn = _fn(gdi32, "CreateRoundRectRgn", wintypes.HRGN, I, I, I, I, I, I)

# shell32 / shcore
Shell_NotifyIconW = _fn(shell32, "Shell_NotifyIconW", wintypes.BOOL, D,
                        ctypes.POINTER(NOTIFYICONDATAW))
GetDpiForMonitor = _fn(shcore, "GetDpiForMonitor", HRESULT, wintypes.HMONITOR, I,
                       ctypes.POINTER(U), ctypes.POINTER(U))

# winmm (waveIn)
HWAVEIN = wintypes.HANDLE
waveInGetNumDevs = _fn(winmm, "waveInGetNumDevs", U)
waveInGetDevCapsW = _fn(winmm, "waveInGetDevCapsW", U, UINT_PTR, ctypes.POINTER(WAVEINCAPSW), U)
waveInOpen = _fn(winmm, "waveInOpen", U, ctypes.POINTER(HWAVEIN), U,
                 ctypes.POINTER(WAVEFORMATEX), ULONG_PTR, ULONG_PTR, D)
waveInPrepareHeader = _fn(winmm, "waveInPrepareHeader", U, HWAVEIN, ctypes.POINTER(WAVEHDR), U)
waveInUnprepareHeader = _fn(winmm, "waveInUnprepareHeader", U, HWAVEIN,
                            ctypes.POINTER(WAVEHDR), U)
waveInAddBuffer = _fn(winmm, "waveInAddBuffer", U, HWAVEIN, ctypes.POINTER(WAVEHDR), U)
waveInStart = _fn(winmm, "waveInStart", U, HWAVEIN)
waveInReset = _fn(winmm, "waveInReset", U, HWAVEIN)
waveInClose = _fn(winmm, "waveInClose", U, HWAVEIN)
waveInGetErrorTextW = _fn(winmm, "waveInGetErrorTextW", U, U, wintypes.LPWSTR, U)


def rgb(r: int, g: int, b: int) -> int:
    """COLORREF from components."""
    return r | (g << 8) | (b << 16)


def enable_dpi_awareness() -> None:
    """Per-monitor DPI awareness, so overlay geometry is in real pixels."""
    with contextlib.suppress(OSError):
        SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
