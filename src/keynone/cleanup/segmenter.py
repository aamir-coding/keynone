"""Split an append-only streaming transcript into finished sentences.

The streaming transducer only ever appends text, so a sentence is final as
soon as the model has started the next one (or the speaker paused). Finished
sentences are handed to the cleanup pipeline while the user keeps talking.
"""

from __future__ import annotations

import os
import re

from keynone.cleanup.rules import ends_with_abbreviation

_BOUNDARY_RE = re.compile(r"[.!?…]+[\"')\]]*\s+(?=\S)")


class SentenceSegmenter:
    def __init__(self) -> None:
        self._seen = ""
        self._consumed = 0

    def update(self, text: str, *, flush: bool = False) -> list[str]:
        """Return sentences completed since the last call.

        ``flush`` (a pause or the end of the dictation) also emits the
        unfinished tail.
        """
        if not text.startswith(self._seen[: self._consumed]):
            # The recognizer revised text we already emitted (should not happen
            # with transducers); continue from the common prefix.
            common = len(os.path.commonprefix([text, self._seen]))
            self._consumed = min(self._consumed, common)
        self._seen = text
        pending = text[self._consumed :]
        sentences: list[str] = []
        start = 0
        for match in _BOUNDARY_RE.finditer(pending):
            candidate = pending[start : match.end()].strip()
            if ends_with_abbreviation(candidate):
                continue
            sentences.append(candidate)
            start = match.end()
        if flush:
            tail = pending[start:].strip()
            if tail:
                sentences.append(tail)
            start = len(pending)
        self._consumed += start
        return [s for s in sentences if s]

    @property
    def pending(self) -> str:
        """Text after the last emitted sentence (still being spoken)."""
        return self._seen[self._consumed :].strip()
