import pytest

from keynone.listener.hotkeys import Action, HotkeyMachine
from keynone.win32.keys import VK_CAPITAL, VK_ESCAPE, VK_LCONTROL, VK_LWIN, Chord, ChordError

D = 0x44  # 'D'
A = 0x41  # 'A'


def machine(chord: str = "ctrl+win", **kw) -> HotkeyMachine:
    return HotkeyMachine(Chord.parse(chord), Chord.parse("esc"), **kw)


def press(m: HotkeyMachine, t: float, *vks: int) -> list[Action]:
    out: list[Action] = []
    for vk in vks:
        out += m.on_key(vk, True, t)[1]
    return out


def release(m: HotkeyMachine, t: float, *vks: int) -> list[Action]:
    out: list[Action] = []
    for vk in vks:
        out += m.on_key(vk, False, t)[1]
    return out


def test_chord_parsing() -> None:
    assert Chord.parse("Ctrl + Win").names == ("ctrl", "win")
    assert Chord.parse("capslock").modifier_only is False
    assert Chord.parse("ctrl+win").needs_menu_mask
    with pytest.raises(ChordError):
        Chord.parse("ctrl+nope")
    with pytest.raises(ChordError):
        Chord.parse("")


def test_hold_to_talk() -> None:
    m = machine()
    assert press(m, 0.0, VK_LCONTROL, VK_LWIN) == [Action.START, Action.MASK]
    assert m.active
    assert release(m, 2.0, VK_LWIN, VK_LCONTROL) == [Action.STOP]
    assert not m.active


def test_other_shortcut_cancels_silently() -> None:
    m = machine()
    press(m, 0.0, VK_LCONTROL, VK_LWIN)
    swallow, actions = m.on_key(D, True, 0.1)  # Ctrl+Win+D (new virtual desktop)
    assert actions == [Action.CANCEL] and swallow is False
    assert release(m, 0.2, D, VK_LWIN, VK_LCONTROL) == []
    assert m.state_name == "IDLE"


def test_single_short_tap_is_discarded_after_timeout() -> None:
    m = machine()
    press(m, 0.0, VK_LCONTROL, VK_LWIN)
    assert release(m, 0.1, VK_LWIN, VK_LCONTROL) == []
    assert m.next_deadline() == pytest.approx(0.5)
    assert m.on_tick(0.3) == []
    assert m.on_tick(0.6) == [Action.CANCEL]
    assert m.state_name == "IDLE"


def test_double_tap_hands_free_then_tap_to_finish() -> None:
    m = machine()
    press(m, 0.0, VK_LCONTROL, VK_LWIN)
    release(m, 0.1, VK_LWIN, VK_LCONTROL)
    assert press(m, 0.25, VK_LCONTROL, VK_LWIN) == [Action.HANDSFREE, Action.MASK]
    assert release(m, 0.3, VK_LWIN, VK_LCONTROL) == []
    assert m.on_tick(5.0) == []  # no timeout while hands-free
    assert press(m, 9.0, VK_LCONTROL, VK_LWIN) == [Action.STOP, Action.MASK]
    release(m, 9.1, VK_LWIN, VK_LCONTROL)
    assert m.state_name == "IDLE"


def test_escape_cancels_and_is_swallowed() -> None:
    m = machine()
    press(m, 0.0, VK_LCONTROL, VK_LWIN)
    swallow, actions = m.on_key(VK_ESCAPE, True, 1.0)
    assert (swallow, actions) == (True, [Action.CANCEL])
    assert release(m, 1.1, VK_LWIN, VK_LCONTROL) == []
    assert m.state_name == "IDLE"


def test_escape_passes_through_when_idle() -> None:
    m = machine()
    assert m.on_key(VK_ESCAPE, True, 0.0) == (False, [])


def test_typing_while_idle_is_untouched() -> None:
    m = machine()
    assert m.on_key(VK_LCONTROL, True, 0.0) == (False, [])
    assert m.on_key(A, True, 0.01) == (False, [])  # Ctrl+A
    assert m.on_key(VK_LWIN, True, 0.02) == (False, [])  # Ctrl+A+Win: not exactly the chord
    assert not m.active


def test_single_key_chord_is_swallowed() -> None:
    m = machine("capslock")
    swallow, actions = m.on_key(VK_CAPITAL, True, 0.0)
    assert swallow and actions == [Action.START]
    swallow, _ = m.on_key(VK_CAPITAL, True, 0.05)  # auto-repeat
    assert swallow
    swallow, actions = m.on_key(VK_CAPITAL, False, 1.0)
    assert swallow and actions == [Action.STOP]


def test_disabled_machine_ignores_everything() -> None:
    m = machine(enabled=False)
    assert press(m, 0.0, VK_LCONTROL, VK_LWIN) == []
