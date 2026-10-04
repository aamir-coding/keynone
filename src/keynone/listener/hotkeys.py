"""Hotkey state machine (pure logic; fed by the low-level keyboard hook).

Gestures for the dictation chord (default Ctrl+Win):

* hold  -> dictate while held, insert on release ("push-to-talk")
* double-tap -> hands-free dictation; one more tap finishes
* a single short tap -> nothing (the recording that started is discarded)
* chord + another key within the hold threshold (e.g. Ctrl+Win+D) -> that was
  a different shortcut: the dictation is cancelled silently
* the cancel key (Esc) during a dictation -> discard it
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from enum import Enum, auto

from keynone.win32.keys import MODIFIER_VKS, Chord


class Action(Enum):
    START = auto()  # begin recording (push-to-talk)
    STOP = auto()  # finish and insert
    CANCEL = auto()  # discard
    HANDSFREE = auto()  # the running dictation became hands-free
    MASK = auto()  # tap the mask key now (stop Win/Alt from opening menus)


class _State(Enum):
    IDLE = auto()
    HOLDING = auto()
    PENDING_TAP = auto()  # released quickly: waiting to see if a second tap follows
    ARMING = auto()  # second tap is down: hands-free starts when it is released
    HANDSFREE = auto()
    IGNORE = auto()  # wait until the chord keys are released, then go idle


@dataclass
class HotkeyMachine:
    chord: Chord
    cancel: Chord | None = None
    hold_ms: int = 300
    double_tap_ms: int = 400
    enabled: bool = True

    _state: _State = field(default=_State.IDLE, init=False)
    _pressed: set[int] = field(default_factory=set, init=False)
    _t_down: float = field(default=0.0, init=False)
    _tap_deadline: float = field(default=0.0, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    @property
    def active(self) -> bool:
        return self._state in (_State.HOLDING, _State.PENDING_TAP, _State.ARMING,
                               _State.HANDSFREE)

    @property
    def state_name(self) -> str:
        return self._state.name

    def reset(self) -> None:
        """Forget the current gesture (e.g. after an automatic stop)."""
        with self._lock:
            self._state = _State.IGNORE if self._chord_keys_down() else _State.IDLE

    def _chord_keys_down(self) -> bool:
        return any(self.chord.involves(vk) for vk in self._pressed)

    def _suppressible(self, vk: int) -> bool:
        # Never swallow modifiers: other shortcuts (Ctrl+C ...) need them.
        return self.chord.involves(vk) and vk not in MODIFIER_VKS

    def on_key(self, vk: int, down: bool, now: float) -> tuple[bool, list[Action]]:
        """Process one physical key event at time ``now`` (seconds).

        Returns ``(swallow, actions)``.
        """
        with self._lock:
            if not self.enabled:
                self._pressed.discard(vk)
                return False, []
            return self._on_key(vk, down, now)

    def _on_key(self, vk: int, down: bool, now: float) -> tuple[bool, list[Action]]:
        repeat = down and vk in self._pressed
        before = self.chord.satisfied_by(self._pressed)
        prev_state = self._state
        if down:
            self._pressed.add(vk)
        else:
            self._pressed.discard(vk)
        after = self.chord.satisfied_by(self._pressed)
        pressed_chord = down and after and not before and not repeat
        released_chord = not down and before and not after
        swallow = self._suppressible(vk) and (prev_state is not _State.IDLE or pressed_chord)
        actions: list[Action] = []
        mask = [Action.MASK] if self.chord.needs_menu_mask else []

        if (down and not repeat and self.cancel is not None and self.cancel.satisfied_by({vk})
                and self.active and not self.chord.involves(vk)):
            self._state = _State.IGNORE if self._chord_keys_down() else _State.IDLE
            return True, [Action.CANCEL]

        if repeat:
            return swallow, []

        state = self._state
        if state is _State.IDLE:
            if pressed_chord:
                self._state, self._t_down = _State.HOLDING, now
                actions = [Action.START, *mask]
        elif state is _State.HOLDING:
            if down and not self.chord.involves(vk):
                if (now - self._t_down) * 1000 < self.hold_ms:
                    self._state = _State.IGNORE  # it was another shortcut
                    actions = [Action.CANCEL]
            elif released_chord:
                if (now - self._t_down) * 1000 >= self.hold_ms:
                    self._state = _State.IGNORE if self._chord_keys_down() else _State.IDLE
                    actions = [Action.STOP]
                else:
                    self._state = _State.PENDING_TAP
                    self._tap_deadline = now + self.double_tap_ms / 1000
        elif state is _State.PENDING_TAP:
            if pressed_chord:
                self._state = _State.ARMING
                actions = [Action.HANDSFREE, *mask]
            elif down and not self.chord.involves(vk):
                self._state = _State.IDLE  # typing resumed: the tap was accidental
                actions = [Action.CANCEL]
        elif state is _State.ARMING:
            if not self._chord_keys_down():
                self._state = _State.HANDSFREE
        elif state is _State.HANDSFREE:
            if pressed_chord:
                self._state = _State.IGNORE
                actions = [Action.STOP, *mask]
        elif state is _State.IGNORE and not self._chord_keys_down():
            self._state = _State.IDLE
        return swallow, actions

    def on_tick(self, now: float) -> list[Action]:
        """Advance timers; call regularly (or at :meth:`next_deadline`)."""
        with self._lock:
            if self._state is _State.PENDING_TAP and now >= self._tap_deadline:
                self._state = _State.IDLE
                return [Action.CANCEL]
            return []

    def next_deadline(self) -> float | None:
        with self._lock:
            return self._tap_deadline if self._state is _State.PENDING_TAP else None
