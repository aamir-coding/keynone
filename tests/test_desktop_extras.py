"""Opt-in desktop tests for smart spacing and speak-to-edit on our own text box.

Run with:  $env:KEYNONE_DESKTOP_TESTS = "1"; pytest tests/test_desktop_extras.py

Safety: keystrokes are only sent after confirming that the test's own window
holds the keyboard focus; the user's clipboard is restored by the code under
test. The speak-to-edit test starts the local LLM (CPU) for a real rewrite.
"""

from __future__ import annotations

import ctypes
import os
import threading
import time
from collections.abc import Iterator

import pytest

from keynone.cleanup.formatting import adapt_to_context
from keynone.config import AppProfile, InsertConfig, load_config
from keynone.engine.inject import Inserter
from keynone.win32 import api, clipboard
from keynone.win32.uia import UiaClient
from keynone.win32.windows import window_info

pytestmark = [
    pytest.mark.desktop,
    pytest.mark.skipif(os.environ.get("KEYNONE_DESKTOP_TESTS") != "1",
                       reason="set KEYNONE_DESKTOP_TESTS=1 to run desktop tests"),
]

EM_SETSEL, WM_SETTEXT, WM_GETTEXT, WM_GETTEXTLENGTH = 0x00B1, 0x000C, 0x000D, 0x000E


class TextBox:
    """Top-level window with a multi-line EDIT control on its own UI thread."""

    def __init__(self, text: str) -> None:
        self.top = self.edit = 0
        self._initial = text
        ready = threading.Event()

        def run() -> None:
            from keynone.win32.gui import run_message_loop

            instance = api.GetModuleHandleW(None)
            self.top = api.CreateWindowExW(0, "STATIC", "Keynone extras test", 0x10CF0000, 220,
                                           220, 560, 240, None, None, instance, None)
            self.edit = api.CreateWindowExW(0, "EDIT", "", 0x50800044, 10, 10, 520, 160,
                                            self.top, None, instance, None)
            self.set_text(self._initial)
            api.SetForegroundWindow(self.top)
            api.SetFocus(self.edit)
            ready.set()
            run_message_loop()

        threading.Thread(target=run, daemon=True).start()
        ready.wait(5)
        time.sleep(0.3)

    def set_text(self, text: str) -> None:
        buffer = ctypes.create_unicode_buffer(text)
        api.SendMessageW(self.edit, WM_SETTEXT, 0, ctypes.addressof(buffer))

    def text(self) -> str:
        length = api.SendMessageW(self.edit, WM_GETTEXTLENGTH, 0, 0)
        buffer = ctypes.create_unicode_buffer(length + 1)
        api.SendMessageW(self.edit, WM_GETTEXT, length + 1, ctypes.addressof(buffer))
        return buffer.value.replace("\r\n", "\n")

    def select(self, start: int, end: int) -> None:
        api.SendMessageW(self.edit, EM_SETSEL, start, end)

    @property
    def focused(self) -> bool:
        return api.GetForegroundWindow() == self.top

    def close(self) -> None:
        api.PostMessageW(self.top, api.WM_CLOSE, 0, 0)
        api.DestroyWindow(self.top)


@pytest.fixture
def uia() -> Iterator[UiaClient]:
    client = UiaClient()
    client.warm_up()
    yield client
    client.close()


def _open(text: str) -> TextBox:
    box = TextBox(text)
    if not box.focused:
        box.close()
        pytest.skip("Windows did not give the test window the keyboard focus")
    return box


def test_smart_spacing_mid_sentence(uia: UiaClient) -> None:
    box = _open("I think")
    try:
        box.select(7, 7)  # caret at the end, right after "think"
        context = uia.caret_context(timeout=5)
        assert context is not None and context.before.endswith("I think")
        text = adapt_to_context("We should go.", context.before, context.after,
                                style="default", trailing_space=True)
        assert box.focused, "focus moved away; aborting before sending keys"
        result = Inserter(InsertConfig()).insert(text, AppProfile(), window_info(box.top))
        time.sleep(0.4)
        assert result.inserted
        assert box.text() == "I think we should go. "
    finally:
        box.close()


def test_smart_spacing_inside_a_sentence(uia: UiaClient) -> None:
    box = _open("Call  today.")
    try:
        box.select(5, 5)  # caret between the two spaces
        context = uia.caret_context(timeout=5)
        assert context is not None
        text = adapt_to_context("John.", context.before, context.after, style="default",
                                trailing_space=True)
        assert box.focused, "focus moved away; aborting before sending keys"
        Inserter(InsertConfig()).insert(text, AppProfile(), window_info(box.top))
        time.sleep(0.4)
        assert box.text() == "Call John today."
    finally:
        box.close()


def test_clipboard_fallback_copies_selection_and_restores(uia: UiaClient) -> None:
    box = _open("alpha beta gamma")
    try:
        box.select(6, 10)  # "beta"
        before = clipboard.get_text()
        assert box.focused, "focus moved away; aborting before sending keys"
        copied = Inserter(InsertConfig()).copy_selection(AppProfile())
        assert copied == "beta"
        assert clipboard.get_text() == before
    finally:
        box.close()


def test_speak_to_edit_rewrites_the_selection(uia: UiaClient) -> None:
    from keynone.cleanup.llm import build_polisher
    from keynone.engine.models import LazyPolisher
    from keynone.engine.overlay import Overlay
    from keynone.engine.session import Session, SessionMeta
    from keynone.paths import get_paths
    from keynone.win32.process import KillOnCloseJob

    config = load_config()
    job = KillOnCloseJob()
    polisher = build_polisher(config.llm.model, get_paths(), gpu="off", job=job)
    assert polisher.server.wait_ready(90)

    class Models:  # the parts of EngineModels the edit flow uses
        llm_enabled = True

        def __init__(self) -> None:
            self.polisher = polisher
            self.llm_ready = threading.Event()
            self.llm_ready.set()
            self.asr_ready = threading.Event()
            self.asr_ready.set()
            self.lazy_polisher = LazyPolisher(self)  # type: ignore[arg-type]

    original = "hey, can't make it to the meeting today, sorry!"
    box = _open(original)
    try:
        box.select(0, -1)
        session = Session(SessionMeta(sid=1, mode="edit"), config=config,
                          models=Models(), overlay=Overlay(enabled=False),  # type: ignore[arg-type]
                          inserter=Inserter(config.insert), history=None,
                          on_finished=lambda outcome: None, uia=uia)
        assert box.focused, "focus moved away; aborting before sending keys"
        outcome = session._deliver_edit("Make it more formal.", 2.0)
        time.sleep(0.5)
        edited = box.text()
        assert outcome.status == "inserted", outcome.message
        assert edited != original and len(edited) > 15
        assert not edited.lower().startswith("hey")
        assert "instruction" not in edited.lower()
    finally:
        box.close()
        polisher.close()
        job.close()
