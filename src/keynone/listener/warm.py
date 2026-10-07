"""When to unload the engine after a dictation (pure logic, no Windows calls).

``keep_warm_seconds`` is how long the engine stays loaded after a dictation. With
smart warm-up it stays longer while you dictate often - 15 minutes after 2
dictations within half an hour, 30 minutes after 3 within an hour - so the next
dictation starts at once instead of waiting ~5 s for the models. It still
unloads when you step away (10 minutes without keyboard or mouse input) and
gets no extension while Windows battery saver is on.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

RECENT_SECONDS, RECENT_WARM = 1800.0, 900.0  # 2+ dictations in 30 min -> 15 min
REGULAR_SECONDS, REGULAR_WARM = 3600.0, 1800.0  # 3+ dictations in an hour -> 30 min
AWAY_SECONDS = 600.0  # no keyboard/mouse input this long: the user stepped away


@dataclass
class WarmPolicy:
    keep_warm: float  # lifecycle.keep_warm_seconds (0: unload right away)
    smart: bool = True  # lifecycle.smart_warm
    _finished: deque[float] = field(default_factory=lambda: deque(maxlen=16), init=False)

    def dictated(self, now: float) -> None:
        """A dictation finished at ``now`` (monotonic seconds)."""
        self._finished.append(now)

    @property
    def last(self) -> float | None:
        return self._finished[-1] if self._finished else None

    def _within(self, now: float, seconds: float) -> int:
        return sum(1 for t in self._finished if now - t <= seconds)

    def warm_for(self, now: float, *, battery_saver: bool = False) -> float:
        """How long after the last dictation the engine stays loaded."""
        if self.keep_warm <= 0 or not self.smart or battery_saver or self.last is None:
            return self.keep_warm
        last = self.last
        if self._within(last, REGULAR_SECONDS) >= 3:
            return max(self.keep_warm, REGULAR_WARM)
        if self._within(last, RECENT_SECONDS) >= 2:
            return max(self.keep_warm, RECENT_WARM)
        return self.keep_warm

    def should_unload(self, now: float, *, idle_seconds: float = 0.0,
                      battery_saver: bool = False) -> bool:
        """Whether a loaded, idle engine should be unloaded now."""
        last = self.last
        if last is None:
            return False  # loaded for a dictation that hasn't finished yet
        since = now - last
        if since >= self.warm_for(now, battery_saver=battery_saver):
            return True
        # Stepped away: what smart warm-up added is not needed (keep_warm still counts).
        return self.smart and idle_seconds >= AWAY_SECONDS and since >= self.keep_warm
