"""Hotkey state machine (pure logic; fed by the low-level keyboard hook).

Gestures for the dictation chord (default Ctrl+Win):

* hold  -> dictate while held, insert on release ("push-to-talk")
* double-tap -> hands-free dictation; one more tap finishes
* a single short tap -> nothing (the recording that started is discarded)
* chord + another key within the hold threshold (e.g. Ctrl+Win+D) -> that was
  a different shortcut: the dictation is cancelled silently
* the cancel key (Esc) during a dictation -> discard it

Speak-to-edit chord (default Ctrl+Win+Alt): hold it, say how to change the
selected text, release. Adding its extra key while already holding the
dictation chord switches the running session to edit mode, so the order in
which the keys are pressed doesn't matter.

Paste-last chord (default Alt+Shift+V): pressing it while no dictation runs pastes
the last dictation again. Its keys are swallowed, so the app doesn't see them. If
it contains the dictation chord (Ctrl+Win+V), the dictation that its first keys
started is cancelled silently.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from enum import Enum, auto

from keynone.win32.keys import MODIFIER_VKS, Chord


class Action(Enum):
    START = auto()  # begin recording (push-to-talk dictation)
    START_EDIT = auto()  # begin recording a speak-to-edit instruction
    TO_EDIT = auto()  # the running session became speak-to-edit
    STOP = auto()  # finish and insert
    CANCEL = auto()  # discard
    HANDSFREE = auto()  # the running dictation became hands-free
    MASK = auto()  # tap the mask key now (stop Win/Alt from opening menus)
    PASTE_LAST = auto()  # paste the last dictation again


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
    edit: Chord | None = None
    paste_last: Chord | None = None

    _state: _State = field(default=_State.IDLE, init=False)
    _editing: bool = field(default=False, init=False)
    _pressed: set[int] = field(default_factory=set, init=False)
    _swallowed: set[int] = field(default_factory=set, init=False)  # paste-last keys held
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

    @property
    def editing(self) -> bool:
        return self._editing

    def reset(self) -> None:
        """Forget the current gesture (e.g. after an automatic stop)."""
        with self._lock:
            self._state = _State.IGNORE if self._chord_keys_down() else _State.IDLE
            self._editing = False

    def _involves(self, vk: int) -> bool:
        return self.chord.involves(vk) or (self.edit is not None and self.edit.involves(vk))

    def _chord_keys_down(self) -> bool:
        return any(self._involves(vk) for vk in self._pressed)

    def _suppressible(self, vk: int) -> bool:
        # Never swallow modifiers: other shortcuts (Ctrl+C ...) need them.
        return self._involves(vk) and vk not in MODIFIER_VKS

    def _mask(self, chord: Chord) -> list[Action]:
        return [Action.MASK] if chord.needs_menu_mask else []

    def _after_release(self) -> _State:
        return _State.IGNORE if self._chord_keys_down() else _State.IDLE

    def on_key(self, vk: int, down: bool, now: float) -> tuple[bool, list[Action]]:
        """Process one physical key event at time ``now`` (seconds).

        Returns ``(swallow, actions)``.
        """
        with self._lock:
            if not self.enabled:
                self._pressed.discard(vk)
                return False, []
            return self._on_key(vk, down, now)

    def _paste_pressed(self, vk: int, down: bool, repeat: bool, before: set[int]) -> bool:
        return (self.paste_last is not None and down and not repeat
                and self.paste_last.satisfied_by(self._pressed)
                and not self.paste_last.satisfied_by(before))

    def _on_key(self, vk: int, down: bool, now: float) -> tuple[bool, list[Action]]:
        repeat = down and vk in self._pressed
        before = set(self._pressed)
        if down:
            self._pressed.add(vk)
        else:
            self._pressed.discard(vk)
        if vk in self._swallowed:  # the rest of a paste-last press: its repeats, its release
            if not down:
                self._swallowed.discard(vk)
            return True, []
        holding = self._state is _State.HOLDING
        if self._paste_pressed(vk, down, repeat, before) and (
                self._state in (_State.IDLE, _State.IGNORE)
                # Ctrl+Win+V right after Ctrl+Win: never once a dictation is under way
                or (holding and not self._involves(vk)
                    and (now - self._t_down) * 1000 < self.hold_ms)):
            assert self.paste_last is not None
            pasted = [Action.PASTE_LAST, *self._mask(self.paste_last)]
            if holding:  # Ctrl+Win (dictation) + V: not a dictation after all
                pasted.insert(0, Action.CANCEL)
                self._editing = False
            self._state = _State.IGNORE if self._chord_keys_down() else _State.IDLE
            if vk not in MODIFIER_VKS:
                self._swallowed.add(vk)
            return vk not in MODIFIER_VKS, pasted
        dict_before, dict_after = (self.chord.satisfied_by(before),
                                   self.chord.satisfied_by(self._pressed))
        edit_before = self.edit is not None and self.edit.satisfied_by(before)
        edit_after = self.edit is not None and self.edit.satisfied_by(self._pressed)
        pressed_dict = down and dict_after and not dict_before and not repeat
        pressed_edit = down and edit_after and not edit_before and not repeat
        if self._editing:
            released = not down and edit_before and not edit_after
        else:
            released = not down and dict_before and not dict_after
        prev_state = self._state
        swallow = self._suppressible(vk) and (prev_state is not _State.IDLE or pressed_dict
                                              or pressed_edit)
        actions: list[Action] = []

        if (down and not repeat and self.cancel is not None and self.cancel.satisfied_by({vk})
                and self.active and not self._involves(vk)):
            self._state = self._after_release()
            self._editing = False
            return True, [Action.CANCEL]

        if repeat:
            return swallow, []

        state = self._state
        if state is _State.IDLE:
            if pressed_edit and self.edit is not None:
                self._state, self._t_down, self._editing = _State.HOLDING, now, True
                actions = [Action.START_EDIT, *self._mask(self.edit)]
            elif pressed_dict:
                self._state, self._t_down, self._editing = _State.HOLDING, now, False
                actions = [Action.START, *self._mask(self.chord)]
        elif state is _State.HOLDING:
            if pressed_edit and not self._editing and self.edit is not None:
                self._editing = True  # e.g. Ctrl+Win first, then Alt
                actions = [Action.TO_EDIT, *self._mask(self.edit)]
            elif down and not self._involves(vk):
                if (now - self._t_down) * 1000 < self.hold_ms:
                    self._state = _State.IGNORE  # it was another shortcut
                    self._editing = False
                    actions = [Action.CANCEL]
            elif released:
                if (now - self._t_down) * 1000 >= self.hold_ms:
                    actions = [Action.STOP]
                    self._state = self._after_release()
                elif self._editing:  # no hands-free for edits: a tap just cancels
                    actions = [Action.CANCEL]
                    self._state = self._after_release()
                    self._editing = False
                else:
                    self._state = _State.PENDING_TAP
                    self._tap_deadline = now + self.double_tap_ms / 1000
        elif state is _State.PENDING_TAP:
            if pressed_edit and self.edit is not None:
                self._state, self._t_down, self._editing = _State.HOLDING, now, True
                actions = [Action.TO_EDIT, *self._mask(self.edit)]
            elif pressed_dict:
                self._state = _State.ARMING
                actions = [Action.HANDSFREE, *self._mask(self.chord)]
            elif down and not self._involves(vk):
                self._state = _State.IDLE  # typing resumed: the tap was accidental
                actions = [Action.CANCEL]
        elif state is _State.ARMING:
            if not self._chord_keys_down():
                self._state = _State.HANDSFREE
        elif state is _State.HANDSFREE:
            if pressed_dict or pressed_edit:
                self._state = _State.IGNORE
                actions = [Action.STOP, *self._mask(self.chord)]
        elif state is _State.IGNORE and not self._chord_keys_down():
            self._state = _State.IDLE
        if self._state in (_State.IDLE, _State.IGNORE) and Action.STOP in actions:
            self._editing = False
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
