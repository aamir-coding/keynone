"""The controls of the settings window: one class per kind of setting.

A control shows one value of the :class:`~keynone.settings.model.SettingsModel`
and reports edits to its host. Input that can't be used is marked on the field
with a short explanation, and Save stays disabled until it is fixed.
"""

from __future__ import annotations

import re
import tkinter as tk
from collections.abc import Callable, Sequence
from tkinter import ttk
from typing import Any, Protocol

from keynone.config import BUILTIN_PROFILES
from keynone.settings.model import HOTKEY_KEYS, SettingsModel, hotkey_problem
from keynone.settings.schema import Field, Option
from keynone.settings.system import System
from keynone.settings.theme import Theme
from keynone.win32.keys import VK_MASK, Chord, key_name, send_menu_mask


class Host(Protocol):
    theme: Theme
    model: SettingsModel
    system: System

    def edited(self, key: str, value: Any) -> None:
        """A new, valid value for ``key``."""

    def problem(self, key: str, message: str | None) -> None:
        """``key``'s input can't be used (``None``: it can again)."""


def set_enabled(widget: tk.Misc, enabled: bool) -> None:
    """Enable or disable a widget and everything inside it."""
    if isinstance(widget, ttk.Widget) and not isinstance(widget, ttk.Frame):
        widget.state(["!disabled"] if enabled else ["disabled"])
    elif isinstance(widget, tk.Text):
        widget.configure(state="normal" if enabled else "disabled")
    for child in widget.winfo_children():
        set_enabled(child, enabled)


class Control:
    """One setting on screen."""

    widget: tk.Widget
    wide = False  # takes the full width of the card, below its label

    def __init__(self, host: Host, field: Field) -> None:
        self.host, self.field, self.key = host, field, field.key

    @property
    def value(self) -> Any:
        return self.host.model.get(self.key)

    def refresh(self) -> None:
        """Show the model's current value again (after Revert or a reload)."""

    def recheck(self) -> None:
        """A setting this one depends on changed: check the input again (and take it
        if it has become valid)."""

    def set_enabled(self, enabled: bool) -> None:
        set_enabled(self.widget, enabled)


# --------------------------------------------------------------------------- simple values


class SwitchControl(Control):
    def __init__(self, host: Host, field: Field, parent: tk.Misc) -> None:
        super().__init__(host, field)
        self.var = tk.BooleanVar(master=parent, value=bool(self.value))
        self.widget = ttk.Checkbutton(parent, variable=self.var, style="Switch.TCheckbutton",
                                      command=lambda: host.edited(self.key, self.var.get()))

    def refresh(self) -> None:
        self.var.set(bool(self.value))


def _decimals(step: float) -> int:
    text = f"{step:g}"
    return len(text.split(".")[1]) if "." in text else 0


class NumberControl(Control):
    """A spin box with its unit; ``percent`` fields store 0.94 and show 94 %."""

    def __init__(self, host: Host, field: Field, parent: tk.Misc) -> None:
        super().__init__(host, field)
        self.percent = field.kind == "percent"
        self.integer = field.kind == "int" or self.percent
        factor = 100 if self.percent else 1
        self.low = (field.minimum if field.minimum is not None else 0) * factor
        self.high = (field.maximum if field.maximum is not None else 1e9) * factor
        step = field.step * factor
        self.decimals = 0 if self.integer else max(1, _decimals(step))
        self.widget = frame = ttk.Frame(parent)
        self.var = tk.StringVar(master=parent)
        self.spin = ttk.Spinbox(frame, from_=self.low, to=self.high, increment=step,
                                textvariable=self.var, width=7, justify="right",
                                format=f"%.{self.decimals}f", command=self._commit)
        self.spin.grid(row=0, column=0)
        unit = "%" if self.percent else field.unit
        if unit:
            ttk.Label(frame, text=unit, style="Muted.TLabel").grid(
                row=0, column=1, padx=(host.theme.px(6), 0))
        self.spin.bind("<KeyRelease>", lambda _e: self._commit())
        self.spin.bind("<FocusOut>", lambda _e: self._tidy())
        self.refresh()

    def _show(self, value: float) -> str:
        if self.percent:
            return str(round(value * 100))
        return str(int(value)) if self.integer else f"{value:.{self.decimals}f}"

    def refresh(self) -> None:
        self.var.set(self._show(self.value))
        self.spin.state(["!invalid"])
        self.host.problem(self.key, None)

    def _parse(self) -> float | str:
        text = self.var.get().strip().replace(",", ".")
        try:
            number = float(text)
        except ValueError:
            return "Enter a number."
        low, high = (f"{v:g}" for v in (self.low, self.high))
        if not self.low <= number <= self.high:
            return f"Enter a number from {low} to {high}."
        if self.integer and number != int(number):
            return "Enter a whole number."
        return number

    def _commit(self) -> None:
        parsed = self._parse()
        if isinstance(parsed, str):
            self.spin.state(["invalid"])
            self.host.problem(self.key, parsed)
            return
        self.spin.state(["!invalid"])
        self.host.problem(self.key, None)
        value: float = parsed / 100 if self.percent else (int(parsed) if self.integer else parsed)
        self.host.edited(self.key, value)

    def _tidy(self) -> None:
        if not isinstance(self._parse(), str):
            self.var.set(self._show(self.value))


def _segmented(options: Sequence[Option]) -> bool:
    return len(options) <= 4 and sum(len(o.label) for o in options) <= 32


class ChoiceControl(Control):
    """A drop-down list, or a row of buttons when there are only a few short options.

    A value from config.toml that isn't one of the options is kept and shown as
    "(custom)", so choosing nothing never changes the file."""

    def __init__(self, host: Host, field: Field, parent: tk.Misc,
                 options: Sequence[Option] | None = None) -> None:
        super().__init__(host, field)
        self.parent = parent
        self.options = list(options if options is not None else field.options)
        self._with_current()
        self.var = tk.StringVar(master=parent)
        if _segmented(self.options):
            self.combo: ttk.Combobox | None = None
            self.widget = frame = ttk.Frame(parent)
            for column, option in enumerate(self.options):
                ttk.Radiobutton(frame, text=option.label, value=self._token(option.value),
                                variable=self.var, style="Segment.TRadiobutton",
                                command=self._picked).grid(
                    row=0, column=column, padx=(0 if column == 0 else host.theme.px(4), 0))
        else:
            width = min(34, max(len(o.label) for o in self.options) + 2)
            self.combo = ttk.Combobox(parent, state="readonly", width=width,
                                      values=[o.label for o in self.options])
            self.combo.bind("<<ComboboxSelected>>", lambda _e: self._picked())
            self.widget = self.combo
        self.refresh()

    @staticmethod
    def _token(value: object) -> str:
        return repr(value)

    def _with_current(self) -> None:
        current = self.value
        if all(o.value != current for o in self.options):
            self.options.append(Option(current, f"{current} (custom)"))

    def set_options(self, options: Sequence[Option]) -> None:
        """New choices (e.g. once the graphics cards are known)."""
        self.options = list(options)
        self._with_current()
        if self.combo is not None:
            self.combo.configure(values=[o.label for o in self.options])
        self.refresh()

    def selected(self) -> Option | None:
        if self.combo is not None:
            index = self.combo.current()
            return self.options[index] if index >= 0 else None
        token = self.var.get()
        return next((o for o in self.options if self._token(o.value) == token), None)

    def _picked(self) -> None:
        option = self.selected()
        if option is not None:
            self.host.edited(self.key, option.value)
        if self.combo is not None:
            self.combo.selection_clear()

    def refresh(self) -> None:
        current = self.value
        if all(o.value != current for o in self.options):
            self.set_options(self.options)
            return
        index = next(i for i, o in enumerate(self.options) if o.value == current)
        if self.combo is not None:
            self.combo.current(index)
        else:
            self.var.set(self._token(current))


# --------------------------------------------------------------------------- hotkeys

_SIDED = {
    "Control_L": ("ctrl", "lctrl"), "Control_R": ("ctrl", "rctrl"),
    "Shift_L": ("shift", "lshift"), "Shift_R": ("shift", "rshift"),
    "Alt_L": ("alt", "lalt"), "Alt_R": ("alt", "ralt"),
    "Win_L": ("win", "lwin"), "Win_R": ("win", "rwin"),
}
_MODIFIER_ORDER = ("ctrl", "win", "alt", "shift")


def key_from_event(keysym: str, keycode: int) -> tuple[str, str] | None:
    """(name, side-specific name) of a Tk key event's key, None if it can't be a hotkey.
    On Windows, Tk's keycode is the virtual-key code the keyboard hook sees too."""
    if keysym in _SIDED:
        return _SIDED[keysym]
    name = key_name(keycode)
    return (name, name) if name else None


def same_keys(a: str, b: str) -> bool:
    """Whether two shortcut texts name the same keys ("ctrl+win" == "Win + Ctrl")."""
    if not a.strip() or not b.strip():
        return not a.strip() and not b.strip()
    try:
        return set(Chord.parse(a).names) == set(Chord.parse(b).names)
    except ValueError:
        return False


def chord_text(keys: Sequence[tuple[str, str]]) -> str:
    """"ctrl+win" from the keys of a recorded combination, in the usual order.

    A single modifier keeps its side ("rctrl"): the right Ctrl key alone is a
    good dictation key, the left one is needed for Ctrl+C and friends."""
    if len(keys) == 1:
        return keys[0][1]
    names = {name for name, _ in keys}
    modifiers = [m for m in _MODIFIER_ORDER if m in names]
    others = [name for name, _ in keys if name not in _MODIFIER_ORDER]
    return "+".join(modifiers + others)


class HotkeyControl(Control):
    """Type a shortcut (with suggestions), or press Record and then the keys."""

    RECORD_SECONDS = 12

    def __init__(self, host: Host, field: Field, parent: tk.Misc) -> None:
        super().__init__(host, field)
        px = host.theme.px
        self.widget = frame = ttk.Frame(parent)
        self.var = tk.StringVar(master=parent, value=self.value)
        self.combo = ttk.Combobox(frame, textvariable=self.var, width=16, style="Mono.TCombobox",
                                  values=[str(o.value) for o in field.options if o.value])
        self.combo.grid(row=0, column=0)
        self.combo.bind("<KeyRelease>", lambda _e: self._commit())
        self.combo.bind("<<ComboboxSelected>>", lambda _e: self._commit())
        self.button = ttk.Button(frame, text="Record", width=9, command=self._record)
        self.button.grid(row=0, column=1, padx=(px(6), 0))
        self.button.bind("<KeyPress>", self._key_down)
        self.button.bind("<KeyRelease>", self._key_up)
        self.button.bind("<FocusOut>", lambda _e: self._stop_recording())
        self._recording = False
        self._held: set[str] = set()
        self._keys: list[tuple[str, str]] = []
        self._before = ""
        self._timer: str | None = None

    def refresh(self) -> None:
        self._stop_recording()
        self.var.set(self.value)
        self._commit()

    def recheck(self) -> None:
        if not self._recording:
            self._commit()

    def _values(self) -> dict[str, Any]:
        values = {k: self.host.model.get(k) for k in HOTKEY_KEYS}
        values[self.key] = self.var.get()
        return values

    def _commit(self) -> None:
        text = self.var.get().strip().lower()
        problem = hotkey_problem(self.key, text, self._values())
        self.combo.state(["invalid" if problem else "!invalid"])
        self.host.problem(self.key, problem)
        if problem is not None:
            return
        saved = self.host.model.saved(self.key)
        # "Ctrl + Win" in the file and "ctrl+win" here are the same keys: no change.
        self.host.edited(self.key, saved if same_keys(text, saved) else
                         "+".join(Chord.parse(text).names) if text else "")

    # ----------------------------------------------------------------- recording

    def _record(self) -> None:
        if self._recording:
            self._stop_recording(keep=False)
            return
        # The running listener would react to its own hotkey: pause it meanwhile.
        self.host.system.suspend_hotkeys(self.RECORD_SECONDS + 3)
        self._recording, self._held, self._keys = True, set(), []
        self._before = self.var.get()
        self.var.set("press keys…")
        self.combo.state(["!invalid"])
        self.host.problem(self.key, None)
        self.button.configure(text="Cancel")
        self.button.focus_set()
        self._timer = self.widget.after(self.RECORD_SECONDS * 1000,
                                        lambda: self._stop_recording(keep=False))

    def _key_down(self, event: tk.Event) -> str | None:
        if not self._recording:
            return None
        if event.keycode == VK_MASK:  # our own menu mask (below), not a key the user pressed
            return "break"
        if event.keysym in ("Win_L", "Win_R", "Alt_L", "Alt_R"):
            send_menu_mask()  # keep the Start menu / window menu from opening on release
        if event.keysym == "Escape" and not self._held and self.key != "hotkeys.cancel":
            self._stop_recording(keep=False)
            return "break"
        key = key_from_event(event.keysym, event.keycode)
        if key is None:
            self.host.problem(self.key, "That key can't be part of a shortcut. Try another.")
            return "break"
        self.host.problem(self.key, None)
        self._held.add(key[1])
        if key not in self._keys:
            self._keys.append(key)
        self.var.set(chord_text(self._keys))
        return "break"

    def _key_up(self, event: tk.Event) -> str | None:
        if not self._recording:
            return None
        if event.keycode == VK_MASK:
            return "break"
        key = key_from_event(event.keysym, event.keycode)
        if key is not None:
            self._held.discard(key[1])
        if not self._held and self._keys:
            self._stop_recording(keep=True)
        return "break"

    def _stop_recording(self, keep: bool = False) -> None:
        if not self._recording:
            return
        self._recording = False
        if self._timer is not None:
            self.widget.after_cancel(self._timer)
            self._timer = None
        self.host.system.resume_hotkeys()
        self.button.configure(text="Record")
        if not keep or not self._keys:
            self.var.set(self._before)
        self._commit()


# --------------------------------------------------------------------------- lists


class _Box(tk.Frame):
    """A 1-pixel border around a widget that has none of its own (tk.Text)."""

    def __init__(self, parent: tk.Misc, theme: Theme) -> None:
        super().__init__(parent, background=theme.palette.border_strong, padx=1, pady=1)


class WordsControl(Control):
    """Dictionary words: one per line in a small text area."""

    wide = True

    def __init__(self, host: Host, field: Field, parent: tk.Misc) -> None:
        super().__init__(host, field)
        p, px = host.theme.palette, host.theme.px
        self.widget = box = _Box(parent, host.theme)
        self.text = tk.Text(box, height=7, width=40, wrap="none", undo=True, relief="flat",
                            borderwidth=0, background=p.surface, foreground=p.text,
                            insertbackground=p.text, selectbackground=p.accent,
                            selectforeground=p.on_accent, font=host.theme.fonts.body,
                            padx=px(8), pady=px(6), highlightthickness=0)
        scroll = ttk.Scrollbar(box, orient="vertical", command=self.text.yview,
                               style="Kn.Vertical.TScrollbar")

        def scrolled(first: float, last: float) -> None:  # the bar only when there's more
            scroll.set(first, last)
            if float(first) <= 0 and float(last) >= 1:
                scroll.pack_forget()
            elif not scroll.winfo_ismapped():
                scroll.pack(side="right", fill="y", before=self.text)

        self.text.configure(yscrollcommand=scrolled)
        self.text.pack(side="left", fill="both", expand=True)
        self.text.bind("<<Modified>>", self._modified)
        self.text.bind("<FocusIn>", lambda _e: box.configure(background=p.accent))
        self.text.bind("<FocusOut>", lambda _e: box.configure(background=p.border_strong))
        self._loading = False
        self.refresh()

    def words(self) -> list[str]:
        seen: list[str] = []
        for line in self.text.get("1.0", "end").splitlines():
            word = line.strip()
            if word and word not in seen:
                seen.append(word)
        return seen

    def _modified(self, _event: object = None) -> None:
        if self.text.edit_modified():
            self.text.edit_modified(False)
            if not self._loading:
                self.host.edited(self.key, self.words())

    def refresh(self) -> None:
        self._loading = True
        self.text.delete("1.0", "end")
        self.text.insert("1.0", "\n".join(self.value))
        self.text.edit_modified(False)
        self.text.edit_reset()
        self._loading = False


class ReplacementsControl(Control):
    """Pairs of "what the recognizer hears" -> "what to write"."""

    wide = True

    def __init__(self, host: Host, field: Field, parent: tk.Misc) -> None:
        super().__init__(host, field)
        px = host.theme.px
        self.widget = frame = ttk.Frame(parent)
        frame.columnconfigure(0, weight=1)
        frame.columnconfigure(2, weight=1)
        self.headers = (ttk.Label(frame, text="When it hears", style="Muted.TLabel"),
                        ttk.Label(frame, text="write", style="Muted.TLabel"))
        self.headers[0].grid(row=0, column=0, sticky="w")
        self.headers[1].grid(row=0, column=2, sticky="w")
        self.rows_frame = frame
        self.rows: list[tuple[tk.StringVar, tk.StringVar, list[tk.Widget]]] = []
        self.add = ttk.Button(frame, text="Add a replacement", command=self._add_row)
        self._gap = px(6)
        self.refresh()

    def _add_row(self, heard: str = "", written: str = "", focus: bool = True) -> None:
        row = len(self.rows) + 1
        heard_var = tk.StringVar(master=self.rows_frame, value=heard)
        written_var = tk.StringVar(master=self.rows_frame, value=written)
        left = ttk.Entry(self.rows_frame, textvariable=heard_var, width=22)
        arrow = ttk.Label(self.rows_frame, text="→", style="Muted.TLabel")
        right = ttk.Entry(self.rows_frame, textvariable=written_var, width=22)
        widgets: list[tk.Widget] = [left, arrow, right]
        entry = (heard_var, written_var, widgets)
        remove = ttk.Button(self.rows_frame, text="Remove", style="Link.TButton",
                            command=lambda: self._remove(entry))
        widgets.append(remove)
        for column, widget in enumerate(widgets):
            widget.grid(row=row, column=column, sticky="we" if column in (0, 2) else "",
                        pady=(self._gap, 0), padx=(0, self._gap))
        for var in (heard_var, written_var):
            var.trace_add("write", lambda *_: self._changed())
        self.rows.append(entry)
        self.add.grid(row=row + 1, column=0, sticky="w", pady=(self._gap * 2, 0))
        self._show_headers()
        if focus:
            left.focus_set()

    def _remove(self, entry: tuple[tk.StringVar, tk.StringVar, list[tk.Widget]]) -> None:
        for widget in entry[2]:
            widget.destroy()
        self.rows.remove(entry)
        self._show_headers()
        self._changed()

    def _show_headers(self) -> None:
        for label in self.headers:
            if self.rows:
                label.grid()
            else:
                label.grid_remove()

    def pairs(self) -> tuple[dict[str, str], str | None]:
        result: dict[str, str] = {}
        problem = None
        for heard_var, written_var, _ in self.rows:
            heard, written = heard_var.get().strip(), written_var.get().strip()
            if not heard and not written:
                continue
            if not heard or not written:
                problem = "Fill in both sides of each replacement."
            elif heard.lower() in (k.lower() for k in result):
                problem = f"“{heard}” is listed twice."
            else:
                result[heard] = written
        return result, problem

    def _changed(self) -> None:
        pairs, problem = self.pairs()
        self.host.problem(self.key, problem)
        if problem is None:
            self.host.edited(self.key, pairs)

    def refresh(self) -> None:
        for _, _, widgets in self.rows:
            for widget in widgets:
                widget.destroy()
        self.rows = []
        for heard, written in self.value.items():
            self._add_row(heard, written, focus=False)
        if not self.rows:
            self.add.grid(row=1, column=0, sticky="w", pady=(0, 0))
        self._show_headers()
        self.host.problem(self.key, None)


# --------------------------------------------------------------------------- snippets

_SNIPPET_LEADS = ("insert", "paste", "type")


class _SnippetCard:
    """One snippet: the phrase to say and the text it inserts (several lines allowed)."""

    def __init__(self, owner: SnippetsControl, phrase: str, text: str) -> None:
        theme = owner.host.theme
        p, px = theme.palette, theme.px
        self.owner = owner
        self.border = tk.Frame(owner.list_frame, background=p.border)
        body = ttk.Frame(self.border, padding=(px(12), px(10)))
        body.pack(fill="both", expand=True, padx=1, pady=1)
        body.columnconfigure(1, weight=1)
        self.phrase = tk.StringVar(master=body, value=phrase)
        ttk.Label(body, text="Say").grid(row=0, column=0, sticky="w", padx=(0, px(12)))
        self.entry = ttk.Entry(body, textvariable=self.phrase, width=28)
        self.entry.grid(row=0, column=1, sticky="w")
        ttk.Button(body, text="Remove", style="Link.TButton",
                   command=lambda: owner.remove(self)).grid(row=0, column=2, sticky="e")
        ttk.Label(body, text="Insert").grid(row=1, column=0, sticky="nw", padx=(0, px(12)),
                                            pady=(px(8), 0))
        box = _Box(body, theme)
        box.grid(row=1, column=1, columnspan=2, sticky="we", pady=(px(8), 0))
        self.text = tk.Text(box, height=3, width=40, wrap="word", undo=True, relief="flat",
                            borderwidth=0, background=p.surface, foreground=p.text,
                            insertbackground=p.text, selectbackground=p.accent,
                            selectforeground=p.on_accent, font=theme.fonts.body,
                            padx=px(8), pady=px(6), highlightthickness=0)
        self.text.pack(fill="both", expand=True)
        self.text.insert("1.0", text)
        self.text.edit_modified(False)
        self.text.edit_reset()
        self.text.bind("<<Modified>>", self._modified)
        self.text.bind("<FocusIn>", lambda _e: box.configure(background=p.accent))
        self.text.bind("<FocusOut>", lambda _e: box.configure(background=p.border_strong))
        self.phrase.trace_add("write", lambda *_: owner.changed())

    def _modified(self, _event: object = None) -> None:
        if self.text.edit_modified():
            self.text.edit_modified(False)
            self.owner.changed()

    def value(self) -> tuple[str, str]:
        return self.phrase.get().strip(), self.text.get("1.0", "end-1c")


def _spoken(phrase: str) -> str:
    """A phrase as it is matched: lowercase words only."""
    return " ".join(re.findall(r"[^\W_]+(?:['’][^\W_]+)*", phrase.lower()))


class SnippetsControl(Control):
    """Saved text and the phrase that inserts it."""

    wide = True

    def __init__(self, host: Host, field: Field, parent: tk.Misc) -> None:
        super().__init__(host, field)
        px = host.theme.px
        self.widget = frame = ttk.Frame(parent)
        frame.columnconfigure(0, weight=1)
        self.list_frame = ttk.Frame(frame)
        self.list_frame.grid(row=0, column=0, sticky="we")
        self.list_frame.columnconfigure(0, weight=1)
        self.empty = ttk.Label(self.list_frame, style="Muted.TLabel", text="No snippets yet.")
        ttk.Button(frame, text="Add a snippet", command=self._add).grid(
            row=1, column=0, sticky="w", pady=(px(10), 0))
        self.cards: list[_SnippetCard] = []
        self._loading = False
        self.refresh()

    def _layout(self) -> None:
        px = self.host.theme.px
        self.empty.grid_forget()
        for row, card in enumerate(self.cards):
            card.border.grid(row=row, column=0, sticky="we", pady=(0 if row == 0 else px(8), 0))
        if not self.cards:
            self.empty.grid(row=0, column=0, sticky="w")

    def _add(self) -> None:
        card = _SnippetCard(self, "", "")
        self.cards.append(card)
        self._layout()
        card.entry.focus_set()
        self.changed()

    def remove(self, card: _SnippetCard) -> None:
        card.border.destroy()
        self.cards.remove(card)
        self._layout()
        self.changed()

    def snippets(self) -> tuple[dict[str, str], str | None]:
        result: dict[str, str] = {}
        seen: set[str] = set()
        problem = None
        for card in self.cards:
            phrase, text = card.value()
            if not phrase and not text.strip():
                continue
            spoken = _spoken(phrase)
            if not spoken:
                problem = problem or "Every snippet needs a phrase to say."
            elif spoken.split()[0] in _SNIPPET_LEADS:
                problem = problem or (f"Leave “{phrase.split()[0]}” out of the phrase: you say "
                                      "it in front of the phrase anyway.")
            elif not text.strip():
                problem = problem or f"“{phrase}” has no text to insert."
            elif spoken in seen:
                problem = problem or f"“{phrase}” is listed twice."
            else:
                seen.add(spoken)
                result[phrase] = text
        return result, problem

    def changed(self) -> None:
        if self._loading:
            return
        values, problem = self.snippets()
        self.host.problem(self.key, problem)
        if problem is None:
            self.host.edited(self.key, values)

    def refresh(self) -> None:
        self._loading = True
        for card in self.cards:
            card.border.destroy()
        self.cards = [_SnippetCard(self, phrase, text) for phrase, text in self.value.items()]
        self._layout()
        self._loading = False
        self.host.problem(self.key, None)


# --------------------------------------------------------------------------- app profiles

_STYLES = (Option("default", "Default"), Option("chat", "Chat"), Option("email", "Email"),
           Option("prompt", "Prompt"), Option("code", "Code"))
_OVERRIDES: tuple[tuple[str, str, tuple[Option, ...]], ...] = (
    ("method", "Insert by", (Option(None, "Default"), Option("paste", "Pasting"),
                             Option("type", "Typing"))),
    ("llm_mode", "AI polish", (Option(None, "Default"), Option("auto", "When needed"),
                               Option("always", "Every dictation"), Option("off", "Never"))),
    ("trailing_space", "Space after", (Option(None, "Default"), Option(True, "Yes"),
                                       Option(False, "No"))),
    ("drop_final_period", "Final period", (Option(None, "Default"), Option(False, "Keep"),
                                           Option(True, "Leave out"))),
)


def _split_list(text: str) -> list[str]:
    return [part.strip() for part in text.replace(";", ",").split(",") if part.strip()]


class _ProfileCard:
    def __init__(self, owner: AppsControl, profile: dict[str, Any]) -> None:
        theme = owner.host.theme
        p, px = theme.palette, theme.px
        self.owner = owner
        self.extra = {k: v for k, v in profile.items()
                      if k in ("paste_keys", "copy_keys")}  # kept, edited in config.toml
        self.border = tk.Frame(owner.list_frame, background=p.border)
        body = ttk.Frame(self.border, padding=(px(12), px(10)))
        body.pack(fill="both", expand=True, padx=1, pady=1)
        body.columnconfigure(1, weight=1)
        self.name = tk.StringVar(master=body, value=profile.get("name", ""))
        self.match = tk.StringVar(master=body, value=", ".join(profile.get("match", [])))
        self.titles = tk.StringVar(master=body, value=", ".join(profile.get("title_match", [])))
        self.style = tk.StringVar(master=body, value=profile.get("style", "default"))
        gap = px(6)

        def label(row: int, text: str) -> None:
            ttk.Label(body, text=text).grid(row=row, column=0, sticky="w", pady=(gap, 0),
                                            padx=(0, px(12)))

        label(0, "Name")
        ttk.Entry(body, textvariable=self.name, width=24).grid(row=0, column=1, sticky="w",
                                                              pady=(gap, 0))
        ttk.Button(body, text="Remove", style="Link.TButton",
                   command=lambda: owner.remove(self)).grid(row=0, column=2, sticky="e")
        label(1, "Programs")
        ttk.Entry(body, textvariable=self.match, style="Mono.TEntry").grid(
            row=1, column=1, columnspan=2, sticky="we", pady=(gap, 0))
        label(2, "Window title contains")
        ttk.Entry(body, textvariable=self.titles).grid(row=2, column=1, columnspan=2,
                                                       sticky="we", pady=(gap, 0))
        label(3, "Style")
        styles = ttk.Frame(body)
        styles.grid(row=3, column=1, columnspan=2, sticky="w", pady=(gap, 0))
        for column, option in enumerate(_STYLES):
            ttk.Radiobutton(styles, text=option.label, value=str(option.value),
                            variable=self.style, style="Segment.TRadiobutton",
                            command=owner.changed).grid(row=0, column=column,
                                                        padx=(0 if column == 0 else px(4), 0))
        overrides = ttk.Frame(body)
        overrides.grid(row=4, column=0, columnspan=3, sticky="w", pady=(px(10), 0))
        self.overrides: dict[str, tuple[ttk.Combobox, tuple[Option, ...]]] = {}
        for column, (key, text, options) in enumerate(_OVERRIDES):
            ttk.Label(overrides, text=text, style="Muted.TLabel").grid(
                row=0, column=column, sticky="w", padx=(0, px(10)))
            combo = ttk.Combobox(overrides, state="readonly", width=14,
                                 values=[o.label for o in options])
            current = profile.get(key)
            combo.current(next((i for i, o in enumerate(options) if o.value == current), 0))
            combo.bind("<<ComboboxSelected>>", self._picked)
            combo.grid(row=1, column=column, sticky="w", padx=(0, px(10)))
            self.overrides[key] = (combo, options)
        for var in (self.name, self.match, self.titles):
            var.trace_add("write", lambda *_: owner.changed())

    def _picked(self, event: tk.Event) -> None:
        if isinstance(event.widget, ttk.Combobox):
            event.widget.selection_clear()
        self.owner.changed()

    def profile(self) -> dict[str, Any]:
        out: dict[str, Any] = {"name": self.name.get().strip()}
        if match := _split_list(self.match.get()):
            out["match"] = match
        if titles := _split_list(self.titles.get()):
            out["title_match"] = titles
        out["style"] = self.style.get()
        for key, (combo, options) in self.overrides.items():
            value = options[max(0, combo.current())].value
            if value is not None:
                out[key] = value
        out.update(self.extra)
        return out

    def problem(self) -> str | None:
        profile = self.profile()
        if not profile["name"]:
            return "Give every profile a name."
        if not profile.get("match") and not profile.get("title_match"):
            return f"“{profile['name']}” needs a program or a window title to apply to."
        return None


class AppsControl(Control):
    """Your app profiles, plus a read-only summary of the built-in ones."""

    wide = True

    def __init__(self, host: Host, field: Field, parent: tk.Misc) -> None:
        super().__init__(host, field)
        px = host.theme.px
        self.widget = frame = ttk.Frame(parent)
        frame.columnconfigure(0, weight=1)
        self.list_frame = ttk.Frame(frame)
        self.list_frame.grid(row=0, column=0, sticky="we")
        self.list_frame.columnconfigure(0, weight=1)
        self.empty = ttk.Label(self.list_frame, style="Muted.TLabel",
                               text="No profiles of your own yet.")
        ttk.Button(frame, text="Add a profile", command=self._add).grid(
            row=1, column=0, sticky="w", pady=(px(10), 0))
        self.cards: list[_ProfileCard] = []
        self.refresh()

    def _layout(self) -> None:
        px = self.host.theme.px
        self.empty.grid_forget()
        for row, card in enumerate(self.cards):
            card.border.grid(row=row, column=0, sticky="we", pady=(0 if row == 0 else px(8), 0))
        if not self.cards:
            self.empty.grid(row=0, column=0, sticky="w")

    def _add(self) -> None:
        self.cards.append(_ProfileCard(self, {"name": f"profile {len(self.cards) + 1}",
                                              "style": "default"}))
        self._layout()
        self.changed()

    def remove(self, card: _ProfileCard) -> None:
        card.border.destroy()
        self.cards.remove(card)
        self._layout()
        self.changed()

    def changed(self) -> None:
        problem = next((p for card in self.cards if (p := card.problem())), None)
        self.host.problem(self.key, problem)
        if problem is None:
            self.host.edited(self.key, [card.profile() for card in self.cards])

    def refresh(self) -> None:
        for card in self.cards:
            card.border.destroy()
        self.cards = [_ProfileCard(self, profile) for profile in self.value]
        self._layout()
        self.host.problem(self.key, None)


def builtin_profiles_text() -> list[tuple[str, str]]:
    """(name, what it matches and does) of the built-in profiles, for the apps page."""
    rows = []
    for profile in BUILTIN_PROFILES:
        apps = [m.removesuffix(".exe") for m in profile.match]
        titles = [f"“{t}”" for t in profile.title_match]
        target = ", ".join(apps[:6] + (["…"] if len(apps) > 6 else []))
        if titles:
            target = (target + "; titles " if target else "titles ") + ", ".join(titles[:4])
        rows.append((profile.name, f"{profile.style} style · {target}"))
    return rows


Builder = Callable[[Host, Field, tk.Misc], Control]

BUILDERS: dict[str, Builder] = {
    "bool": SwitchControl,
    "int": NumberControl,
    "float": NumberControl,
    "percent": NumberControl,
    "choice": ChoiceControl,
    "hotkey": HotkeyControl,
    "words": WordsControl,
    "replacements": ReplacementsControl,
    "snippets": SnippetsControl,
    "apps": AppsControl,
}
