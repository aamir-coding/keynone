"""The History page of the settings window: recent dictations from data/history.db.

The engine writes the history; this page only reads it, searches it, copies an
entry and deletes entries on request. Nothing here is sent anywhere.
"""

from __future__ import annotations

import contextlib
import logging
import sqlite3
import time
import tkinter as tk
from collections.abc import Callable
from tkinter import messagebox, ttk

from keynone.engine.history import Dictation, History
from keynone.settings.system import System
from keynone.settings.theme import Theme

log = logging.getLogger(__name__)

PAGE_SIZE = 50
_MAX_CHARS = 280  # longer dictations are shortened in the list (Copy takes all of it)


def when(stamp: float, now: float | None = None) -> str:
    """ "Today 14:32", "Yesterday 09:10", "Mon 3 Oct, 18:02", "3 Oct 2025, 18:02"."""
    moment = time.localtime(stamp)
    today = time.localtime(now if now is not None else time.time())
    clock = time.strftime("%H:%M", moment)
    days = (time.mktime((today.tm_year, today.tm_mon, today.tm_mday, 0, 0, 0, 0, 0, -1))
            - time.mktime((moment.tm_year, moment.tm_mon, moment.tm_mday, 0, 0, 0, 0, 0, -1)))
    days = round(days / 86400)
    if days == 0:
        return f"Today {clock}"
    if days == 1:
        return f"Yesterday {clock}"
    day = str(moment.tm_mday)
    if moment.tm_year == today.tm_year:
        return f"{time.strftime('%a', moment)} {day} {time.strftime('%b', moment)}, {clock}"
    return f"{day} {time.strftime('%b %Y', moment)}, {clock}"


def details(item: Dictation, now: float | None = None) -> str:
    """The muted line under a dictation: when, where, how long, how it went in."""
    parts = [when(item.created_at, now)]
    if item.app:
        parts.append(item.app.removesuffix(".exe").removesuffix(".EXE"))
    if item.audio_seconds:
        seconds = item.audio_seconds
        parts.append(f"{seconds:.1f} s" if seconds < 10 else f"{seconds:.0f} s")
    if item.method == "edit":
        parts.append("speak-to-edit")
    elif item.method in ("clipboard", "none"):
        parts.append("copied, not typed")
    return " · ".join(parts)


def shorten(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= _MAX_CHARS else text[:_MAX_CHARS].rsplit(" ", 1)[0] + " …"


class HistoryView:
    """Search box, the list of dictations and their actions, inside ``parent``."""

    def __init__(self, parent: ttk.Frame, *, theme: Theme, system: System,
                 enabled: Callable[[], bool], show_privacy: Callable[[], None],
                 status: Callable[[str], None]) -> None:
        self.theme, self.system = theme, system
        self._enabled, self._show_privacy, self._status = enabled, show_privacy, status
        p, px = theme.palette, theme.px
        self.parent = parent
        self._history: History | None = None
        self._shown = PAGE_SIZE
        self._latest = -1
        self._search_job: str | None = None

        bar = ttk.Frame(parent, style="Page.TFrame")
        bar.grid(row=parent.grid_size()[1], column=0, sticky="we", pady=(0, px(10)))
        bar.columnconfigure(1, weight=1)
        ttk.Label(bar, text="Search", style="Page.TLabel").grid(row=0, column=0,
                                                                padx=(0, px(8)))
        self.query = tk.StringVar(master=parent)
        self.search = ttk.Entry(bar, textvariable=self.query, width=32)
        self.search.grid(row=0, column=1, sticky="w")
        self.query.trace_add("write", lambda *_: self._search_soon())
        self.clear_button = ttk.Button(bar, text="Clear history", command=self.clear)
        self.clear_button.grid(row=0, column=2, sticky="e")
        self.summary = ttk.Label(parent, style="PageMuted.TLabel")
        self.summary.grid(row=parent.grid_size()[1], column=0, sticky="w", pady=(0, px(8)))

        border = tk.Frame(parent, background=p.border)
        border.grid(row=parent.grid_size()[1], column=0, sticky="we")
        self.list = ttk.Frame(border)
        self.list.pack(fill="both", expand=True, padx=1, pady=1)
        self.list.columnconfigure(0, weight=1)
        self.more = ttk.Button(parent, text="Show more", command=self._show_more)
        self._more_row = parent.grid_size()[1]

    # ----------------------------------------------------------------- data

    def _open(self) -> History | None:
        """history.db, if there is one (the page never creates it)."""
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
        """(Re)load the list: when the page is shown, after a search or a change."""
        for child in self.list.winfo_children():
            child.destroy()
        history = self._open()
        query = self.query.get().strip()
        try:
            items = history.recent(self._shown + 1, query=query) if history else []
            total = history.count() if history else 0
            self._latest = history.latest_id() if history else 0
        except sqlite3.Error as exc:
            log.exception("reading the history failed")
            items, total = [], 0
            self._status(f"Couldn't read the history: {exc}")
        more = len(items) > self._shown
        items = items[:self._shown]
        self._summarize(total, query, len(items))
        if not items:
            self._empty(query)
        for index, item in enumerate(items):
            self._row(item, first=index == 0)
        if more:
            self.more.grid(row=self._more_row, column=0, sticky="w",
                           pady=(self.theme.px(10), 0))
        else:
            self.more.grid_remove()
        self.clear_button.state(["!disabled"] if total else ["disabled"])

    def poll(self) -> None:
        """Called every few seconds while the page is shown: new dictations appear."""
        history = self._open()
        try:
            latest = history.latest_id() if history else 0
        except sqlite3.Error:
            return
        if latest != self._latest:
            self.refresh()

    def _summarize(self, total: int, query: str, shown: int) -> None:
        if not self._enabled():
            text = "The history is off: new dictations aren't kept."
        elif query:
            text = f"{shown} of {total} dictations contain “{query}”." if shown else ""
        else:
            text = (f"{total} dictation{'s' * (total != 1)}, stored only on this computer "
                    "(data/history.db).") if total else ""
        self.summary.configure(text=text)
        if text:
            self.summary.grid()
        else:
            self.summary.grid_remove()

    def _empty(self, query: str) -> None:
        px = self.theme.px
        frame = ttk.Frame(self.list, padding=(px(16), px(14)))
        frame.grid(row=0, column=0, sticky="we")
        if query:
            text = f"No dictations contain “{query}”."
        elif self._enabled():
            text = "No dictations yet. They show up here after you dictate."
        else:
            text = "The history is off, so dictations aren't kept."
        ttk.Label(frame, text=text, style="Muted.TLabel").pack(side="left")
        if not self._enabled() and not query:
            ttk.Button(frame, text="Turn it on", style="Link.TButton",
                       command=self._show_privacy).pack(side="left", padx=(px(6), 0))

    def _row(self, item: Dictation, *, first: bool) -> None:
        p, px = self.theme.palette, self.theme.px
        if not first:
            tk.Frame(self.list, background=p.border, height=1).grid(
                row=self.list.grid_size()[1], column=0, sticky="we")
        row = ttk.Frame(self.list, padding=(px(16), px(10)))
        row.grid(row=self.list.grid_size()[1], column=0, sticky="we")
        row.columnconfigure(0, weight=1)
        ttk.Label(row, text=shorten(item.final_text) or "(a line break)", wraplength=px(470),
                  justify="left").grid(row=0, column=0, sticky="w")
        ttk.Label(row, text=details(item), style="Muted.TLabel").grid(
            row=1, column=0, sticky="w", pady=(px(2), 0))
        actions = ttk.Frame(row)
        actions.grid(row=0, column=1, rowspan=2, sticky="ne", padx=(px(16), 0))
        ttk.Button(actions, text="Copy", style="Link.TButton", width=0,
                   command=lambda: self.copy(item)).pack(side="left")
        ttk.Button(actions, text="Delete", style="Link.TButton", width=0,
                   command=lambda: self.delete(item)).pack(side="left", padx=(px(10), 0))

    # ----------------------------------------------------------------- actions

    def _search_soon(self) -> None:
        root = self.parent.winfo_toplevel()  # timers live on the window, like its others
        if self._search_job is not None:
            root.after_cancel(self._search_job)
        self._shown = PAGE_SIZE

        def run() -> None:
            self._search_job = None
            self.refresh()

        self._search_job = root.after(250, run)

    def _show_more(self) -> None:
        self._shown += PAGE_SIZE
        self.refresh()

    def copy(self, item: Dictation) -> None:
        try:
            self.system.copy_text(item.final_text.strip())
        except Exception as exc:  # the clipboard can be busy (another app holds it)
            self._status(f"Couldn't copy: {exc}")
            return
        self._status("Copied - press Ctrl+V to paste it.")

    def delete(self, item: Dictation) -> None:
        history = self._open()
        if history is None:
            return
        with contextlib.suppress(sqlite3.Error):
            history.delete(item.id)
        self.refresh()
        self._status("Deleted.")

    def clear(self) -> None:
        history = self._open()
        if history is None or not messagebox.askyesno(
                "Keynone settings", "Delete every dictation in the history?\n\nThis can't be "
                "undone.", parent=self.parent, icon="warning"):
            return
        try:
            history.clear()
        except sqlite3.Error as exc:
            self._status(f"Couldn't clear the history: {exc}")
            return
        self.refresh()
        self._status("History cleared.")
