"""UI Automation client: text around the caret, the selection, password fields.

Used for context-aware insertion (spacing/casing at the cursor) and for
speak-to-edit (reading the selected text without touching the clipboard).

All UIA calls run on one dedicated multi-threaded-apartment thread (the
recommended setup for UIA clients that own windows), and every request has a
hard timeout, so an unresponsive application can never stall a dictation.
Apps without text support simply return ``None`` and callers fall back.
"""

from __future__ import annotations

import concurrent.futures
import logging
import queue
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

log = logging.getLogger(__name__)

T = TypeVar("T")

UIA_TEXT_PATTERN_ID = 10014
UIA_TEXT_PATTERN2_ID = 10024
ENDPOINT_START, ENDPOINT_END = 0, 1
UNIT_CHARACTER = 0
_OBJECT_REPLACEMENT = "￼"  # embedded images/controls in rich text


@dataclass(frozen=True)
class CaretContext:
    before: str  # text before the caret (or before the selection)
    after: str  # text after the caret (or after the selection)
    selection: str  # selected text, "" when nothing is selected
    is_password: bool


def _clean(text: Any) -> str:
    return str(text or "").replace(_OBJECT_REPLACEMENT, "").replace("\r\n", "\n").replace(
        "\r", "\n"
    )


class UiaClient:
    def __init__(self) -> None:
        self._requests: queue.Queue[tuple[Callable[[], Any], concurrent.futures.Future[Any]] |
                                    None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._uia: Any = None
        self._automation: Any = None
        self.available = True
        # Text boxes being watched for corrections (keynone.learning), by handle. Only
        # touched on the UIA thread: COM objects stay with the thread that made them.
        self._watched: dict[int, Any] = {}
        self._next_handle = 0

    # ----------------------------------------------------------------- worker

    def _ensure_thread(self) -> None:
        with self._lock:
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="uia", daemon=True)
                self._thread.start()

    def _run(self) -> None:
        try:
            # Multi-threaded apartment for this thread; must precede the first
            # comtypes import, which initializes COM for the importing thread.
            sys.coinit_flags = 0  # type: ignore[attr-defined]
            import comtypes
            import comtypes.client

            comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
            self._uia = comtypes.client.GetModule("UIAutomationCore.dll")
            self._automation = comtypes.client.CreateObject(
                self._uia.CUIAutomation8, interface=self._uia.IUIAutomation2
            )
            # Never wait long for an unresponsive application.
            self._automation.ConnectionTimeout = 600
            self._automation.TransactionTimeout = 600
        except Exception:
            log.warning("UI Automation unavailable; context-aware insertion disabled",
                        exc_info=True)
            self.available = False
        while True:
            item = self._requests.get()
            if item is None:
                return
            func, future = item
            if not future.set_running_or_notify_cancel():
                continue
            try:
                future.set_result(func() if self.available else None)
            except Exception as exc:  # app-specific UIA failures are expected
                future.set_exception(exc)

    def _call(self, func: Callable[[], T], timeout: float) -> T | None:
        if not self.available:
            return None
        self._ensure_thread()
        future: concurrent.futures.Future[T] = concurrent.futures.Future()
        self._requests.put((func, future))
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            future.cancel()
            log.info("UI Automation call timed out after %.2fs", timeout)
        except Exception as exc:
            log.debug("UI Automation call failed: %s", exc)
        return None

    def warm_up(self) -> None:
        """Start the UIA thread now (first use generates COM wrappers, ~1 s)."""
        self._ensure_thread()

    def check(self, timeout: float = 10.0) -> bool:
        """True once UI Automation is initialized (reads no application content)."""
        return bool(self._call(lambda: True, timeout))

    def close(self) -> None:
        if self._thread is not None:
            self._requests.put(None)

    # ----------------------------------------------------------------- queries (UIA thread)

    def _text_pattern(self, element: Any) -> tuple[Any, Any] | None:
        """(TextPattern, TextPattern2 or None) of an element, if it supports text."""
        uia = self._uia
        unknown = element.GetCurrentPattern(UIA_TEXT_PATTERN2_ID)
        if unknown:
            pattern2 = unknown.QueryInterface(uia.IUIAutomationTextPattern2)
            return pattern2, pattern2
        unknown = element.GetCurrentPattern(UIA_TEXT_PATTERN_ID)
        if unknown:
            return unknown.QueryInterface(uia.IUIAutomationTextPattern), None
        return None

    def _focused(self) -> Any:
        element = self._automation.GetFocusedElement()
        return element if element else None

    def _context(self, before_chars: int, after_chars: int,
                 max_selection: int) -> CaretContext | None:
        element = self._focused()
        if element is None:
            return None
        is_password = bool(element.CurrentIsPassword)
        patterns = self._text_pattern(element)
        if patterns is None:
            return CaretContext("", "", "", is_password) if is_password else None
        pattern, pattern2 = patterns
        current = None
        ranges = pattern.GetSelection()
        if ranges and ranges.Length > 0:
            current = ranges.GetElement(0)
        elif pattern2 is not None:
            caret = pattern2.GetCaretRange()
            current = caret[-1] if isinstance(caret, tuple) else caret
        if not current:
            return None
        selection = _clean(current.GetText(max_selection))
        before = current.Clone()
        before.MoveEndpointByRange(ENDPOINT_END, before, ENDPOINT_START)
        before.MoveEndpointByUnit(ENDPOINT_START, UNIT_CHARACTER, -before_chars)
        after = current.Clone()
        after.MoveEndpointByRange(ENDPOINT_START, after, ENDPOINT_END)
        after.MoveEndpointByUnit(ENDPOINT_END, UNIT_CHARACTER, after_chars)
        return CaretContext(
            before=_clean(before.GetText(before_chars + 8))[-before_chars:],
            after=_clean(after.GetText(after_chars + 8))[:after_chars],
            selection=selection,
            is_password=is_password,
        )

    # ----------------------------------------------------------------- public API

    def caret_context(self, *, timeout: float = 0.35, before_chars: int = 80,
                      after_chars: int = 16, max_selection: int = 20_000) -> CaretContext | None:
        """Text around the caret of the focused element, or None if unsupported."""
        return self._call(lambda: self._context(before_chars, after_chars, max_selection),
                          timeout)

    def selected_text(self, *, timeout: float = 0.6, max_chars: int = 20_000) -> str | None:
        """The focused element's selected text ("" = none, None = unknown)."""
        context = self.caret_context(timeout=timeout, max_selection=max_chars)
        return None if context is None else context.selection

    # ----------------------------------------------------------------- watching a text box

    def _watch(self) -> int | None:
        element = self._focused()
        if element is None or element.CurrentIsPassword:
            return None
        patterns = self._text_pattern(element)
        if patterns is None:
            return None
        self._next_handle += 1
        self._watched[self._next_handle] = patterns[0]
        return self._next_handle

    def _watched_text(self, handle: int, max_chars: int) -> str | None:
        pattern = self._watched.get(handle)
        if pattern is None:
            return None
        return _clean(pattern.DocumentRange.GetText(max_chars))

    def watch_focused(self, *, timeout: float = 0.5) -> int | None:
        """Remember the focused text box (not a password field) to read it again later;
        a handle for :meth:`watched_text`, or None if it has no text support."""
        return self._call(self._watch, timeout)

    def watched_text(self, handle: int, *, timeout: float = 0.5,
                     max_chars: int = 100_000) -> str | None:
        """The whole text of a watched text box now (None if it is gone)."""
        return self._call(lambda: self._watched_text(handle, max_chars), timeout)

    def unwatch(self, handle: int) -> None:
        self._call(lambda: self._watched.pop(handle, None), 0.5)
