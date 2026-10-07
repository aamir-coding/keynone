"""The "Learned from your corrections" card on the settings window's Dictionary page."""

from __future__ import annotations

import functools
import tkinter as tk
from collections.abc import Callable
from tkinter import messagebox, ttk

from keynone.settings.system import System
from keynone.settings.theme import Theme


class LearnedView:
    """The spellings Keynone learned, each with Remove, inside ``card``."""

    def __init__(self, card: ttk.Frame, *, theme: Theme, system: System,
                 status: Callable[[str], None]) -> None:
        self.theme, self.system, self._status = theme, system, status
        self.body = ttk.Frame(card)
        self.body.grid(row=0, column=0, sticky="we")
        self.body.columnconfigure(0, weight=1)
        self._seen = -1.0

    def refresh(self) -> None:
        p, px = self.theme.palette, self.theme.px
        for child in self.body.winfo_children():
            child.destroy()
        self._seen = self.system.learned_changed_at()
        entries = self.system.learned()
        if not entries:
            ttk.Label(self.body, style="Muted.TLabel", wraplength=px(560), justify="left",
                      text="Nothing learned yet. When you fix a name right after dictating it, "
                           "your spelling shows up here.").grid(
                row=0, column=0, sticky="w", padx=px(16), pady=px(12))
            return
        for index, item in enumerate(entries):
            if index:
                tk.Frame(self.body, background=p.border, height=1).grid(
                    row=self.body.grid_size()[1], column=0, sticky="we")
            row = ttk.Frame(self.body, padding=(px(16), px(9)))
            row.grid(row=self.body.grid_size()[1], column=0, sticky="we")
            row.columnconfigure(1, weight=1)
            ttk.Label(row, text=item.written, style="Strong.TLabel").grid(row=0, column=0,
                                                                          sticky="w")
            ttk.Label(row, text=f"heard as “{item.heard}”", style="Muted.TLabel").grid(
                row=0, column=1, sticky="w", padx=(px(10), 0))
            ttk.Button(row, text="Remove", style="Link.TButton", width=0,
                       command=functools.partial(self.remove, item.heard)).grid(
                row=0, column=2, sticky="e")
        footer = ttk.Frame(self.body, padding=(px(16), px(4), px(16), px(10)))
        footer.grid(row=self.body.grid_size()[1], column=0, sticky="we")
        ttk.Button(footer, text="Remove all", style="Link.TButton", width=0,
                   command=self.remove_all).pack(side="right")

    def poll(self) -> None:
        """Called every few seconds while the page is shown: newly learned ones appear."""
        if self.system.learned_changed_at() != self._seen:
            self.refresh()

    def remove(self, heard: str) -> None:
        self.system.forget(heard)
        self.refresh()
        self._status(f"Removed “{heard}”. Keynone writes it as it hears it again.")

    def remove_all(self) -> None:
        if not messagebox.askyesno("Keynone settings", "Forget every spelling Keynone learned "
                                   "from your corrections?", parent=self.body):
            return
        self.system.forget()
        self.refresh()
        self._status("Forgot every learned spelling.")
