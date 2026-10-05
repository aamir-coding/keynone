"""Clipboard access for paste-based insertion.

Keynone's temporary clipboard content is tagged so that Windows clipboard
history (Win+V), cloud clipboard sync and well-behaved clipboard managers
ignore it; the user's previous clipboard is restored right after pasting
(unless they copied something new in the meantime).
"""

from __future__ import annotations

import ctypes
import logging
import time
from dataclasses import dataclass, field

from keynone.win32 import api

log = logging.getLogger(__name__)

CF_TEXT, CF_BITMAP, CF_METAFILEPICT, CF_OEMTEXT = 1, 2, 3, 7
CF_PALETTE, CF_UNICODETEXT, CF_ENHMETAFILE = 9, 13, 14
GMEM_MOVEABLE = 0x0002

# Formats whose data is a GDI handle, not HGLOBAL memory: cannot be copied
# byte-wise (Windows re-synthesizes CF_BITMAP from CF_DIB anyway).
_NON_HGLOBAL = frozenset({CF_BITMAP, CF_METAFILEPICT, CF_PALETTE, CF_ENHMETAFILE})
_SYNTHESIZED = frozenset({CF_TEXT, CF_OEMTEXT})  # derived from CF_UNICODETEXT
_MAX_SNAPSHOT_BYTES = 64 * 1024 * 1024


class ClipboardBusyError(RuntimeError):
    pass


def _register(name: str) -> int:
    return int(api.RegisterClipboardFormatW(name))


@dataclass
class Snapshot:
    formats: list[tuple[int, bytes]] = field(default_factory=list)
    complete: bool = True


class _Opened:
    """Context manager: open the clipboard, retrying while another app holds it."""

    def __enter__(self) -> _Opened:
        for _ in range(50):
            if api.OpenClipboard(None):
                return self
            time.sleep(0.01)
        raise ClipboardBusyError("clipboard is in use by another application")

    def __exit__(self, *exc: object) -> None:
        api.CloseClipboard()


def _hglobal_from(data: bytes) -> int:
    handle = api.GlobalAlloc(GMEM_MOVEABLE, max(len(data), 1))
    if not handle:
        raise MemoryError("GlobalAlloc failed")
    pointer = api.GlobalLock(handle)
    if not pointer:
        api.GlobalFree(handle)
        raise MemoryError("GlobalLock failed")
    try:
        ctypes.memmove(pointer, data, len(data))
    finally:
        api.GlobalUnlock(handle)
    return int(handle)


def _set_data(fmt: int, data: bytes) -> None:
    handle = _hglobal_from(data)
    if not api.SetClipboardData(fmt, handle):
        api.GlobalFree(handle)  # ownership only transfers on success
        raise OSError(f"SetClipboardData({fmt}) failed: {ctypes.get_last_error()}")


def _mark_private() -> None:
    """Keep the current content out of clipboard history and cloud sync."""
    zero = (0).to_bytes(4, "little")
    for name in ("ExcludeClipboardContentFromMonitorProcessing", "CanIncludeInClipboardHistory",
                 "CanUploadToCloudClipboard"):
        try:
            _set_data(_register(name), zero)
        except OSError:
            log.debug("could not set %s", name)


def snapshot() -> Snapshot:
    """Copy all byte-copyable clipboard formats."""
    snap = Snapshot()
    total = 0
    with _Opened():
        fmt = 0
        while True:
            fmt = api.EnumClipboardFormats(fmt)
            if not fmt:
                break
            if fmt in _NON_HGLOBAL or fmt in _SYNTHESIZED:
                continue
            handle = api.GetClipboardData(fmt)
            if not handle:
                continue
            size = api.GlobalSize(handle)
            pointer = api.GlobalLock(handle)
            if not pointer:
                snap.complete = False
                continue
            try:
                total += size
                if total > _MAX_SNAPSHOT_BYTES:
                    log.info("clipboard too large to preserve (%d bytes)", total)
                    return Snapshot(complete=False)
                snap.formats.append((fmt, ctypes.string_at(pointer, size)))
            finally:
                api.GlobalUnlock(handle)
    return snap


def set_text(text: str, *, private: bool = True) -> int:
    """Put ``text`` on the clipboard; returns the clipboard sequence number."""
    data = text.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-16-le") + b"\x00\x00"
    with _Opened():
        api.EmptyClipboard()
        _set_data(CF_UNICODETEXT, data)
        if private:
            _mark_private()
    return int(api.GetClipboardSequenceNumber())


def sequence_number() -> int:
    """Changes whenever any application writes to the clipboard."""
    return int(api.GetClipboardSequenceNumber())


def get_text() -> str | None:
    with _Opened():
        handle = api.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return None
        pointer = api.GlobalLock(handle)
        if not pointer:
            return None
        try:
            return ctypes.wstring_at(pointer).replace("\r\n", "\n")
        finally:
            api.GlobalUnlock(handle)


def restore(snap: Snapshot, expected_sequence: int) -> bool:
    """Restore ``snap`` unless the clipboard changed since we set it."""
    if int(api.GetClipboardSequenceNumber()) != expected_sequence:
        log.info("clipboard changed by the user; not restoring")
        return False
    with _Opened():
        api.EmptyClipboard()
        for fmt, data in snap.formats:
            try:
                _set_data(fmt, data)
            except OSError:
                log.debug("could not restore clipboard format %s", fmt)
        if snap.formats:
            _mark_private()  # already in history from when the user copied it
    return True
