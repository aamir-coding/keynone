"""Watches the text box a dictation went into, for the user's corrections.

Reads it a few times during the minute after the insertion (UI Automation; the
text is compared and dropped, never stored or logged). A correction is learned
once it has stayed the same for two reads in a row, so a word still being typed
("Aami...") is not learned half-way.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Protocol

from keynone.learning import Learned, LearnedStore, corrections

log = logging.getLogger(__name__)


class TextWatching(Protocol):
    """What the watcher needs from :class:`keynone.win32.uia.UiaClient`."""

    def watch_focused(self, *, timeout: float = 0.5) -> int | None: ...
    def watched_text(self, handle: int, *, timeout: float = 0.5,
                     max_chars: int = 100_000) -> str | None: ...
    def unwatch(self, handle: int) -> None: ...


class CorrectionWatcher:
    CHECKS = (4.0, 10.0, 18.0, 30.0, 45.0, 60.0)  # seconds after the insertion

    def __init__(self, uia: TextWatching, store: LearnedStore,
                 on_learned: Callable[[list[Learned]], None]) -> None:
        self._uia = uia
        self._store = store
        self._on_learned = on_learned
        self._stop: threading.Event | None = None
        self._lock = threading.Lock()

    def watch(self, inserted: str, checks: tuple[float, ...] | None = None) -> None:
        """Start watching the focused text box, which ``inserted`` just went into."""
        self.stop()
        handle = self._uia.watch_focused()
        if handle is None:
            return  # no text support (or a password field): nothing to learn from
        stop = threading.Event()
        with self._lock:
            self._stop = stop
        threading.Thread(target=self._run, args=(handle, inserted, stop, checks or self.CHECKS),
                         name="corrections", daemon=True).start()

    def stop(self) -> None:
        """A new dictation starts: the text box may change for other reasons now."""
        with self._lock:
            if self._stop is not None:
                self._stop.set()
                self._stop = None

    def _run(self, handle: int, inserted: str, stop: threading.Event,
             checks: tuple[float, ...]) -> None:
        started = time.monotonic()
        seen: set[Learned] = set()  # found by the previous read
        learned: set[str] = set()
        try:
            for at in checks:
                if stop.wait(max(0.0, started + at - time.monotonic())):
                    return
                text = self._uia.watched_text(handle)
                if text is None:
                    return  # the text box is gone
                found = set(corrections(inserted, text))
                stable = [item for item in found & seen if item.heard not in learned]
                seen = found
                new = [item for item in stable if self._store.add(item)]
                learned |= {item.heard for item in stable}
                if new:
                    log.info("learned %d spelling(s) from a correction", len(new))
                    self._on_learned(new)
        except Exception:  # learning is a bonus: never worth an error message
            log.warning("watching for corrections failed", exc_info=True)
        finally:
            self._uia.unwatch(handle)
