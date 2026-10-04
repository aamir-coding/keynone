"""Insert the final text into whatever app the user is typing in.

Default: put the text on the clipboard (kept out of clipboard history), send
the app's paste shortcut, then restore the user's previous clipboard. Paste
is fast, keeps Unicode/newlines intact, is a single undo step, and never
"sends" chat messages early the way typed Enter keys would. Typing via
Unicode keystrokes is available per app for programs that block paste.
"""

from __future__ import annotations

import contextlib
import logging
import time
from dataclasses import dataclass

from keynone.config import AppProfile, InsertConfig
from keynone.win32 import clipboard, keys
from keynone.win32.process import is_elevated
from keynone.win32.windows import WindowInfo

log = logging.getLogger(__name__)

_NO_TEXT_TARGETS = frozenset({"progman", "workerw", "shell_traywnd"})


@dataclass(frozen=True)
class InsertResult:
    method: str  # "paste" | "type" | "clipboard" | "none"
    inserted: bool
    message: str = ""


class Inserter:
    def __init__(self, config: InsertConfig) -> None:
        self.config = config
        self._self_elevated = is_elevated()

    def _to_clipboard(self, text: str, why: str) -> InsertResult:
        try:
            clipboard.set_text(text, private=False)  # the user will paste it themselves
        except clipboard.ClipboardBusyError:
            return InsertResult("none", False, f"{why} - clipboard busy, see history")
        return InsertResult("clipboard", False, f"{why} - copied, press Ctrl+V")

    def insert(self, text: str, profile: AppProfile, target: WindowInfo) -> InsertResult:
        if not text.strip():
            return InsertResult("none", False, "Nothing to insert")
        if target.password_field:
            return InsertResult("none", False, "Password field - not inserted")
        if not target.is_valid or target.class_name.lower() in _NO_TEXT_TARGETS:
            return self._to_clipboard(text, "No text field focused")
        if not self._self_elevated and is_elevated(target.pid):
            return self._to_clipboard(text, "Admin window")
        if not keys.wait_for_modifiers_released(1.5):
            return self._to_clipboard(text, "Hotkey still held")

        method = profile.method or self.config.method
        try:
            if method == "type":
                newline = "shift+enter" if profile.style == "chat" else "enter"
                keys.type_text(text, newline=newline)
                return InsertResult("type", True)
            return self._paste(text, profile)
        except (OSError, clipboard.ClipboardBusyError) as exc:
            log.warning("insertion failed: %s", exc)
            return self._to_clipboard(text, "Insertion failed")

    def _paste(self, text: str, profile: AppProfile) -> InsertResult:
        snapshot = None
        if self.config.restore_clipboard:
            try:
                snapshot = clipboard.snapshot()
            except clipboard.ClipboardBusyError:
                log.info("clipboard busy; previous content will not be restored")
        sequence = clipboard.set_text(text)
        keys.send_chord(keys.Chord.parse(profile.paste_keys or self.config.paste_keys))
        if snapshot is not None and snapshot.complete:
            # The target app reads the clipboard asynchronously; give it time.
            time.sleep(self.config.restore_delay_ms / 1000)
            with contextlib.suppress(clipboard.ClipboardBusyError):
                clipboard.restore(snapshot, sequence)
        return InsertResult("paste", True)
