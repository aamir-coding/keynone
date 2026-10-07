"""The Home page of the settings window: is Keynone running, the shortcuts, and how
much you have dictated (from the daily counts in data/history.db)."""

from __future__ import annotations

import logging
import random
import sqlite3
import time
import tkinter as tk
from collections.abc import Callable
from tkinter import ttk

from keynone.engine.history import History, Stats
from keynone.settings.system import System
from keynone.settings.theme import Theme
from keynone.win32.keys import Chord, ChordError

log = logging.getLogger(__name__)

TYPING_WPM = 40  # an average typing speed, to estimate the time dictating saved

TIPS = (
    "Say \"literal comma\" to type the word instead of the mark.",
    "\"Insert my address\" mid-sentence puts a snippet right there (Snippets page).",
    "In terminals and editors, \"camel case user name\" becomes userName.",
    "Select text, hold the edit shortcut and say \"make it shorter\".",
    "\"Scratch that\" removes what you said since your last pause.",
    "Say \"new paragraph\" to start one, also in the middle of a sentence.",
    "\"jane dot doe at example dot com\" is written as an e-mail address.",
)


def time_saved(words: int, audio_seconds: float, typing_wpm: int = TYPING_WPM) -> float:
    """Seconds saved by speaking instead of typing the same words."""
    return max(0.0, words / typing_wpm * 60 - audio_seconds)


def duration(seconds: float) -> str:
    """ "under a minute", "12 min", "2 h 15 min", "3 h"."""
    minutes = round(seconds / 60)
    if minutes < 1:
        return "under a minute"
    if minutes < 60:
        return f"{minutes} min"
    hours, rest = divmod(minutes, 60)
    return f"{hours} h {rest} min" if rest else f"{hours} h"


def speaking_speed(words: int, audio_seconds: float) -> int:
    """Words per minute while dictating (0 when there is too little to tell)."""
    return round(words / (audio_seconds / 60)) if audio_seconds >= 10 else 0


def _blend(color: str, background: str, amount: float) -> str:
    a = [int(color[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(background[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x * amount + y * (1 - amount)):02x}" for x, y in zip(a, b,
                                                                                      strict=True))


class HomeView:
    """Status, shortcuts and statistics, inside ``parent`` (a page frame)."""

    def __init__(self, parent: ttk.Frame, *, theme: Theme, system: System,
                 setting: Callable[[str], object], card: Callable[[str], ttk.Frame],
                 show_section: Callable[[str], None]) -> None:
        self.theme, self.system = theme, system
        self._setting, self._show_section = setting, show_section
        self._history: History | None = None
        self._latest = -1
        px = theme.px

        status_card = card("")
        self.status = ttk.Frame(status_card, padding=(px(16), px(12)))
        self.status.grid(row=0, column=0, sticky="we")
        self.status.columnconfigure(1, weight=1)
        self.dot = tk.Label(self.status, text="●", font=theme.fonts.body,
                            background=theme.palette.surface)
        self.dot.grid(row=0, column=0, padx=(0, px(8)))
        self.state = ttk.Label(self.status, style="Strong.TLabel")
        self.state.grid(row=0, column=1, sticky="w")
        self.start = ttk.Button(self.status, text="Start Keynone", style="Accent.TButton",
                                command=self._start)
        self.hint = ttk.Label(self.status, style="Muted.TLabel", wraplength=px(560),
                              justify="left")
        self.hint.grid(row=1, column=1, sticky="w", pady=(px(2), 0))

        self.keys = card("Shortcuts")
        self.keys.columnconfigure(1, weight=1)

        stats = card("Your dictation")
        self.stats = ttk.Frame(stats, padding=(px(16), px(14)))
        self.stats.grid(row=0, column=0, sticky="we")
        for column in range(4):
            self.stats.columnconfigure(column, weight=1, uniform="tile")

        self.tip = ttk.Label(parent, style="PageMuted.TLabel", wraplength=px(600),
                             justify="left", text=f"Tip: {random.choice(TIPS)}")
        self.tip.grid(row=parent.grid_size()[1], column=0, sticky="w", pady=(px(18), 0))

    # ----------------------------------------------------------------- data

    def _open(self) -> History | None:
        if self._history is None and self.system.paths.history_db.exists():
            try:
                self._history = History(self.system.paths.history_db, enabled=True,
                                        retention_days=0)
            except sqlite3.Error:
                log.exception("could not open the history")
        return self._history

    def close(self) -> None:
        if self._history is not None:
            self._history.close()
            self._history = None

    # ----------------------------------------------------------------- showing

    def refresh(self) -> None:
        self._show_status()
        self._show_keys()
        self._show_stats()

    def poll(self) -> None:
        """Called every few seconds while the page is shown."""
        self._show_status()
        history = self._open()
        try:
            latest = history.latest_id() if history else 0
        except sqlite3.Error:
            return
        if latest != self._latest:
            self._show_stats()

    def _show_status(self) -> None:
        p = self.theme.palette
        running = self.system.listener_running()
        self.dot.configure(foreground=p.ok if running else p.faint)
        dictate = self._chord("hotkeys.dictate")
        if running:
            self.state.configure(text="Keynone is running")
            self.hint.configure(text=f"Hold {dictate} anywhere and speak; let go to insert. "
                                     "The microphone icon in the tray has the menu.")
            self.start.grid_remove()
        else:
            self.state.configure(text="Keynone isn't running")
            self.hint.configure(text="Start it to dictate. It can also start when you sign in "
                                     "(General).")
            self.start.grid(row=0, column=2, rowspan=2, sticky="e")

    def _chord(self, key: str) -> str:
        value = str(self._setting(key) or "")
        try:
            return str(Chord.parse(value)) if value else ""
        except ChordError:
            return value

    def _keycaps(self, parent: tk.Misc, chord: str) -> tk.Frame:
        """ "Ctrl+Win" as two small key caps."""
        p, px = self.theme.palette, self.theme.px
        frame = tk.Frame(parent, background=p.surface)
        for index, name in enumerate(chord.split("+")):
            if index:
                tk.Label(frame, text="+", background=p.surface, foreground=p.muted,
                         font=self.theme.fonts.small).pack(side="left", padx=px(3))
            tk.Label(frame, text=name, background=p.bg, foreground=p.text,
                     font=self.theme.fonts.small, padx=px(7), pady=px(1),
                     highlightthickness=1, highlightbackground=p.border_strong,
                     highlightcolor=p.border_strong).pack(side="left")
        return frame

    def _show_keys(self) -> None:
        px, p = self.theme.px, self.theme.palette
        for child in self.keys.winfo_children():
            child.destroy()
        dictate, edit = self._chord("hotkeys.dictate"), self._chord("hotkeys.edit")
        rows = [("Dictate", "hold, and let go to insert", dictate),
                ("Hands-free", "double-tap; tap once more to finish", dictate)]
        if edit:
            rows.append(("Change selected text", "hold and say how: \"make it shorter\"", edit))
        if paste := self._chord("hotkeys.paste_last"):
            rows.append(("Paste the last dictation again", "", paste))
        if cancel := self._chord("hotkeys.cancel"):
            rows.append(("Cancel a dictation", "", cancel))
        for index, (what, note, keys) in enumerate(rows):
            if index:
                tk.Frame(self.keys, background=p.border, height=1).grid(
                    row=self.keys.grid_size()[1], column=0, columnspan=3, sticky="we")
            row = self.keys.grid_size()[1]
            ttk.Label(self.keys, text=what).grid(row=row, column=0, sticky="w",
                                                 padx=(px(16), px(12)), pady=px(9))
            ttk.Label(self.keys, text=note, style="Muted.TLabel").grid(row=row, column=1,
                                                                       sticky="w")
            self._keycaps(self.keys, keys).grid(row=row, column=2, sticky="e",
                                                padx=(px(12), px(16)))

    def _show_stats(self) -> None:
        for child in self.stats.winfo_children():
            child.destroy()
        px = self.theme.px
        if not self._setting("history.enabled"):
            self._note("Statistics come from the history, which is off.", "Turn it on",
                       lambda: self._show_section("privacy"))
            return
        history = self._open()
        try:
            stats = history.stats() if history else Stats()
            self._latest = history.latest_id() if history else 0
        except sqlite3.Error:
            log.exception("reading the statistics failed")
            stats = Stats()
        if not stats.dictations:
            self._note("Your statistics show up here after your first dictation.")
            return
        saved = time_saved(stats.words, stats.audio_seconds)
        wpm = speaking_speed(stats.words, stats.audio_seconds)
        since = time.strftime("%d %b %Y", time.strptime(stats.since, "%Y-%m-%d")).lstrip("0")
        tiles = [
            (f"{stats.words:,}", "words dictated", f"{stats.week_words:,} in the last 7 days"),
            (duration(saved), "saved", f"compared with typing at {TYPING_WPM} words a minute"),
            (f"{wpm} wpm" if wpm else "–", "speaking speed",
             "while dictating" if wpm else "after a few more dictations"),
            (f"{stats.dictations:,}", "dictations", f"since {since}"),
        ]
        for column, (value, caption, note) in enumerate(tiles):
            tile = ttk.Frame(self.stats)
            tile.grid(row=0, column=column, sticky="nw", padx=(0 if column == 0 else px(12), 0))
            ttk.Label(tile, text=value, font=self.theme.fonts.title).pack(anchor="w")
            ttk.Label(tile, text=caption, style="Strong.TLabel").pack(anchor="w")
            ttk.Label(tile, text=note, style="Muted.TLabel", wraplength=px(140),
                      justify="left").pack(anchor="w", pady=(px(2), 0))
        self._chart(stats)

    def _note(self, text: str, button: str = "", action: Callable[[], None] | None = None) -> None:
        frame = ttk.Frame(self.stats)
        frame.grid(row=0, column=0, columnspan=4, sticky="w")
        ttk.Label(frame, text=text, style="Muted.TLabel").pack(side="left")
        if button and action:
            ttk.Button(frame, text=button, style="Link.TButton", command=action).pack(
                side="left", padx=(self.theme.px(6), 0))

    def _chart(self, stats: Stats) -> None:
        """Words per day for the last two weeks, today in full colour."""
        p, px, f = self.theme.palette, self.theme.px, self.theme.fonts
        height, label = px(64), px(18)
        canvas = tk.Canvas(self.stats, height=height + label + px(6), background=p.surface,
                           highlightthickness=0, borderwidth=0)
        canvas.grid(row=1, column=0, columnspan=4, sticky="we", pady=(px(18), 0))
        top = max((words for _, words in stats.days), default=0) or 1

        def draw(_event: object = None) -> None:
            canvas.delete("all")
            width = canvas.winfo_width()
            count = len(stats.days)
            if width < 50 or not count:
                return
            slot = width / count
            bar = min(px(22), slot * 0.62)
            faded = _blend(p.accent, p.surface, 0.42)
            for index, (day, words) in enumerate(stats.days):
                x = index * slot + (slot - bar) / 2
                today = index == count - 1
                if words:
                    tall = max(px(3), height * words / top)
                    canvas.create_rectangle(x, height - tall, x + bar, height,
                                            fill=p.accent if today else faded, width=0)
                else:
                    canvas.create_line(x, height - 1, x + bar, height - 1, fill=p.border)
                weekday = time.strftime("%a", time.strptime(day, "%Y-%m-%d"))[0]
                canvas.create_text(x + bar / 2, height + px(4), anchor="n", text=weekday,
                                   fill=p.text if today else p.muted, font=f.small)

        canvas.bind("<Configure>", draw)

    # ----------------------------------------------------------------- actions

    def _start(self) -> None:
        self.system.start_listener()
        self.state.configure(text="Starting Keynone…")
        self.start.grid_remove()
