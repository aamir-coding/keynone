"""The settings window: ``keynone settings``, or *Settings...* in the tray menu.

It edits ``data/config.toml`` and nothing else (plus the optional Startup
shortcut). The listener picks up saved changes within a second. Everything here
can still be done from a text editor and the command line.
"""

from __future__ import annotations

import contextlib
import ctypes
import logging
import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass, field
from tkinter import messagebox, ttk
from typing import Any

from keynone import __version__
from keynone.cleanup.language import LANGUAGE_NAMES
from keynone.config import ConfigError
from keynone.editions import LANGUAGES, MULTILINGUAL_MODEL
from keynone.paths import Paths, get_paths
from keynone.settings.history_view import HistoryView
from keynone.settings.home_view import HomeView
from keynone.settings.learned_view import LearnedView
from keynone.settings.model import HOTKEY_KEYS, SettingsModel
from keynone.settings.schema import SECTIONS, Field, Option, Section
from keynone.settings.system import GPU_RUNTIME, System
from keynone.settings.theme import Palette, Theme, style_title_bar
from keynone.settings.widgets import BUILDERS, ChoiceControl, Control, builtin_profiles_text

log = logging.getLogger(__name__)

TITLE = "Keynone settings"


@dataclass
class _Row:
    """The label side of one setting: its name, help, problems and notes."""

    frame: ttk.Frame
    label: ttk.Label
    problem: ttk.Label
    note: ttk.Frame
    note_text: ttk.Label
    note_button: ttk.Button
    note_action: list[Callable[[], None]] = field(default_factory=list)


@dataclass
class _Page:
    frame: ttk.Frame
    advanced: list[tk.Widget] = field(default_factory=list)


class ScrollArea(ttk.Frame):
    """A vertically scrolling frame (Tk has none built in)."""

    def __init__(self, parent: tk.Misc, theme: Theme) -> None:
        super().__init__(parent, style="Page.TFrame")
        self.canvas = tk.Canvas(self, background=theme.palette.bg, highlightthickness=0,
                                borderwidth=0, yscrollincrement=theme.px(20))
        self.bar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview,
                                 style="Kn.Vertical.TScrollbar")
        self.inner = ttk.Frame(self.canvas, style="Page.TFrame")
        self._window = self.canvas.create_window(0, 0, anchor="nw", window=self.inner)
        self.canvas.configure(yscrollcommand=self._scrolled)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner.bind("<Configure>", lambda _e: self._resize())
        self.canvas.bind("<Configure>", self._canvas_resized)

    def _canvas_resized(self, event: tk.Event) -> None:
        self.canvas.itemconfigure(self._window, width=event.width)
        self._resize()

    def _resize(self) -> None:
        self.canvas.configure(scrollregion=(0, 0, self.inner.winfo_reqwidth(),
                                            self.inner.winfo_reqheight()))

    def _scrolled(self, first: float, last: float) -> None:
        self.bar.set(first, last)
        if float(first) <= 0 and float(last) >= 1:
            self.bar.pack_forget()
        elif not self.bar.winfo_ismapped():
            self.bar.pack(side="right", fill="y")

    def wheel(self, event: tk.Event) -> None:
        if self.inner.winfo_reqheight() > self.canvas.winfo_height():
            self.canvas.yview_scroll(-3 if event.delta > 0 else 3, "units")

    def to_top(self) -> None:
        self.canvas.yview_moveto(0)


class SettingsApp:
    def __init__(self, root: tk.Tk, paths: Paths, *, palette: Palette | None = None,
                 system: System | None = None) -> None:
        self.root = root
        self.paths = paths
        self.system = system or System(paths)
        self.model = SettingsModel(paths.config_file, paths.default_config)
        self.theme = Theme(root, palette)
        self.controls: dict[str, Control] = {}
        self.rows: dict[str, _Row] = {}
        self.problems: dict[str, str] = {}
        self.pages: dict[str, _Page] = {}
        self.nav: dict[str, tuple[tk.Frame, tk.Frame, tk.Label]] = {}
        self.section_id = ""
        self.show_advanced = tk.BooleanVar(master=root, value=False)
        self.autostart_saved = self.system.autostart()
        self.autostart = tk.BooleanVar(master=root, value=self.autostart_saved)
        self._status_job: str | None = None
        self._disk_notice_shown = False
        self._gpus: list[str] | None = None
        self.history: HistoryView | None = None  # built when the page is first shown
        self.home: HomeView | None = None
        self.learned: LearnedView | None = None
        self._build_shell()
        self.show_section("home")
        # Built now though not shown, so a shortcut in the file that can't be used is
        # flagged (and blocks saving) from the start.
        self.pages["general"] = self._build_section(self._section("general"))
        self._show_file_error()
        self._update_footer()
        self.root.after(1500, self._watch)

    # ================================================================= shell

    def _build_shell(self) -> None:
        root, theme = self.root, self.theme
        p, px = theme.palette, theme.px
        root.title(TITLE)
        # At 175 % on a 1080p laptop the design size would not fit the screen.
        screen_w, screen_h = root.winfo_screenwidth() - 40, root.winfo_screenheight() - 90
        root.minsize(min(px(780), screen_w), min(px(520), screen_h))
        root.geometry(f"{min(px(940), screen_w)}x{min(px(680), screen_h)}")
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.columnconfigure(1, weight=1)
        root.rowconfigure(0, weight=1)

        sidebar = tk.Frame(root, background=p.sidebar, width=px(212))
        sidebar.grid(row=0, column=0, rowspan=2, sticky="nsw")
        sidebar.grid_propagate(False)
        sidebar.columnconfigure(0, weight=1)
        sidebar.rowconfigure(2, weight=1)
        brand = tk.Frame(sidebar, background=p.sidebar)
        brand.grid(row=0, column=0, sticky="we", padx=px(18), pady=(px(20), px(14)))
        tk.Label(brand, image=theme.brand_image(26), background=p.sidebar).grid(
            row=0, column=0, rowspan=2, padx=(0, px(10)))
        tk.Label(brand, text="Keynone", font=theme.fonts.brand, background=p.sidebar,
                 foreground=p.text).grid(row=0, column=1, sticky="sw")
        self.edition_label = tk.Label(brand, font=theme.fonts.small, background=p.sidebar,
                                      foreground=p.muted)
        self.edition_label.grid(row=1, column=1, sticky="nw")
        nav = tk.Frame(sidebar, background=p.sidebar)
        nav.grid(row=1, column=0, sticky="we", pady=(px(6), 0))
        nav.columnconfigure(1, weight=1)
        for row, (section_id, title) in enumerate(
                [("home", "Home"), *[(s.id, s.title) for s in SECTIONS],
                 ("history", "History"), ("about", "About")]):
            self._nav_item(nav, row, section_id, title)
        bottom = tk.Frame(sidebar, background=p.sidebar)
        bottom.grid(row=3, column=0, sticky="we", padx=px(18), pady=(0, px(16)))
        self.listener_label = tk.Label(bottom, font=theme.fonts.small, background=p.sidebar,
                                       foreground=p.muted, anchor="w", justify="left")
        self.listener_label.pack(fill="x", pady=(0, px(10)))
        ttk.Checkbutton(bottom, text=" Show advanced settings", variable=self.show_advanced,
                        style="Sidebar.Switch.TCheckbutton",
                        command=self._apply_advanced).pack(anchor="w")
        link = tk.Label(bottom, text="Edit config.toml", font=theme.fonts.small,
                        background=p.sidebar, foreground=p.accent, cursor="hand2")
        link.pack(anchor="w", pady=(px(8), 0))
        link.bind("<Button-1>", lambda _e: self.system.open(self.paths.config_file))

        content = ttk.Frame(root, style="Page.TFrame")
        content.grid(row=0, column=1, sticky="nsew")
        self.banner = tk.Frame(content, background=p.note_bg)
        self.banner_text = tk.Label(self.banner, background=p.note_bg, foreground=p.note_text,
                                    font=theme.fonts.body, justify="left", anchor="w",
                                    wraplength=px(520))
        self.banner_text.pack(side="left", fill="x", expand=True, padx=px(16), pady=px(10))
        self.banner_buttons = tk.Frame(self.banner, background=p.note_bg)
        self.banner_buttons.pack(side="right", padx=px(12))
        self.scroll = ScrollArea(content, theme)
        self.scroll.pack(fill="both", expand=True)

        footer = tk.Frame(root, background=p.surface)
        footer.grid(row=1, column=1, sticky="we")
        tk.Frame(footer, background=p.border, height=1).pack(side="top", fill="x")
        bar = ttk.Frame(footer, padding=(px(28), px(12)))
        bar.pack(fill="x")
        self.status = ttk.Label(bar, style="Muted.TLabel")
        self.status.pack(side="left")
        self.save_button = ttk.Button(bar, text="Save", style="Accent.TButton", command=self.save)
        self.save_button.pack(side="right")
        self.revert_button = ttk.Button(bar, text="Revert", command=self.revert)
        self.revert_button.pack(side="right", padx=(0, px(8)))

        root.bind("<Control-s>", lambda _e: self.save())
        root.bind("<Control-Tab>", lambda _e: self._step(1))
        with contextlib.suppress(tk.TclError):
            root.bind("<Control-Shift-Tab>", lambda _e: self._step(-1))
        root.bind_all("<MouseWheel>", self._wheel)
        for widget_class in ("TSpinbox", "TCombobox"):  # the page scrolls, values don't change
            root.unbind_class(widget_class, "<MouseWheel>")
        self._update_sidebar()

    def _nav_item(self, parent: tk.Frame, row: int, section_id: str, title: str) -> None:
        p, px = self.theme.palette, self.theme.px
        item = tk.Frame(parent, background=p.sidebar)
        item.grid(row=row, column=1, sticky="we", padx=(px(10), px(12)), pady=px(1))
        marker = tk.Frame(item, background=p.sidebar, width=px(3))
        marker.pack(side="left", fill="y", pady=px(8))
        label = tk.Label(item, text=title, background=p.sidebar, foreground=p.text,
                         font=self.theme.fonts.body, anchor="w", padx=px(9), pady=px(6))
        label.pack(side="left", fill="x", expand=True)

        def click(_event: tk.Event) -> None:
            self.show_section(section_id)

        def enter(_event: tk.Event) -> None:
            self._hover(section_id, True)

        def leave(_event: tk.Event) -> None:
            self._hover(section_id, False)

        for widget in (item, label, marker):
            widget.bind("<Button-1>", click)
            widget.bind("<Enter>", enter)
            widget.bind("<Leave>", leave)
        self.nav[section_id] = (marker, item, label)

    def _hover(self, section_id: str, inside: bool) -> None:
        if section_id == self.section_id:
            return
        p = self.theme.palette
        marker, item, label = self.nav[section_id]
        color = p.hover if inside else p.sidebar
        for widget in (marker, item, label):
            widget.configure(background=color)

    def _paint_nav(self) -> None:
        p, f = self.theme.palette, self.theme.fonts
        for section_id, (marker, item, label) in self.nav.items():
            current = section_id == self.section_id
            color = p.selected if current else p.sidebar
            marker.configure(background=p.accent if current else color)
            item.configure(background=color)
            label.configure(background=color, font=f.strong if current else f.body)

    def _step(self, direction: int) -> str:
        order = list(self.nav)
        index = (order.index(self.section_id) + direction) % len(order)
        self.show_section(order[index])
        return "break"

    def _wheel(self, event: tk.Event) -> None:
        widget = event.widget
        if isinstance(widget, tk.Text | tk.Listbox):
            return  # it scrolls itself
        if isinstance(widget, tk.Misc) and str(widget).startswith(str(self.scroll)):
            self.scroll.wheel(event)

    # ================================================================= pages

    def show_section(self, section_id: str) -> None:
        if section_id == self.section_id:
            return
        if self.section_id:
            self.pages[self.section_id].frame.pack_forget()
        self.section_id = section_id
        if section_id not in self.pages:
            builders: dict[str, Callable[[], _Page]] = {"home": self._build_home,
                                                         "about": self._build_about,
                                                         "history": self._build_history}
            builder = builders.get(section_id)
            self.pages[section_id] = (builder() if builder is not None
                                      else self._build_section(self._section(section_id)))
        self.pages[section_id].frame.pack(fill="both", expand=True)
        self._paint_nav()
        self.scroll.to_top()
        view = self._live_view()
        if view is not None:
            view.refresh()

    def _live_view(self) -> HomeView | HistoryView | LearnedView | None:
        """The page shown now, if it shows what happens outside the window."""
        if self.section_id == "home":
            return self.home
        if self.section_id == "history":
            return self.history
        if self.section_id == "dictionary":
            return self.learned
        return None

    @staticmethod
    def _section(section_id: str) -> Section:
        return next(s for s in SECTIONS if s.id == section_id)

    def _page(self, title: str, description: str) -> _Page:
        px = self.theme.px
        frame = ttk.Frame(self.scroll.inner, style="Page.TFrame",
                          padding=(px(28), px(24), px(28), px(28)))
        frame.columnconfigure(0, weight=1)
        ttk.Label(frame, text=title, style="PageTitle.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(frame, text=description, style="PageMuted.TLabel", wraplength=px(600),
                  justify="left").grid(row=1, column=0, sticky="w", pady=(px(2), px(16)))
        return _Page(frame)

    def _card(self, page: _Page, title: str = "") -> ttk.Frame:
        """A bordered group of rows below what the page has so far; returns its body."""
        px, p = self.theme.px, self.theme.palette
        row = page.frame.grid_size()[1]
        widgets: list[tk.Widget] = []
        if title:
            heading = ttk.Label(page.frame, text=title, style="Group.TLabel")
            heading.grid(row=row, column=0, sticky="w", pady=(px(18), px(6)))
            widgets.append(heading)
        border = tk.Frame(page.frame, background=p.border)
        border.grid(row=row + 1, column=0, sticky="we")
        inner = ttk.Frame(border)
        inner.pack(fill="both", expand=True, padx=1, pady=1)
        inner.columnconfigure(0, weight=1)
        widgets.append(border)
        if title == "Advanced":
            page.advanced.extend(widgets)
        return inner

    def _row(self, card: ttk.Frame, label: str, help_text: str, *, first: bool) -> _Row:
        px, p = self.theme.px, self.theme.palette
        if not first:
            tk.Frame(card, background=p.border, height=1).grid(
                row=card.grid_size()[1], column=0, columnspan=2, sticky="we")
        frame = ttk.Frame(card, padding=(px(16), px(11)))
        frame.grid(row=card.grid_size()[1], column=0, columnspan=2, sticky="we")
        frame.columnconfigure(0, weight=1)
        name = ttk.Label(frame, text=label)
        name.grid(row=0, column=0, sticky="w")
        if help_text:
            ttk.Label(frame, text=help_text, style="Muted.TLabel", wraplength=px(370),
                      justify="left").grid(row=1, column=0, sticky="w", pady=(px(1), 0))
        problem = ttk.Label(frame, style="Error.TLabel", wraplength=px(370), justify="left")
        note = ttk.Frame(frame)
        note_text = ttk.Label(note, style="Muted.TLabel", wraplength=px(370), justify="left")
        note_text.pack(side="left")
        row = _Row(frame, name, problem, note, note_text, ttk.Button(note))
        row.note_button.configure(style="Link.TButton",
                                  command=lambda: [action() for action in row.note_action])
        return row

    def _build_section(self, section: Section) -> _Page:
        page = self._page(section.title, section.description)
        for advanced in (False, True):
            fields = [f for f in section.fields if f.advanced == advanced]
            extras = section.id == "general" and not advanced
            if not fields and not extras:
                continue
            card = self._card(page, "Advanced" if advanced else "")
            first = True
            if extras:
                self._autostart_row(card)
                first = False
            for f in fields:
                self._field_row(card, f, first=first)
                first = False
        if section.id == "apps":
            self._builtin_profiles(page)
        if section.id == "dictionary":
            self.learned = LearnedView(self._card(page, "Learned from your corrections"),
                                       theme=self.theme, system=self.system,
                                       status=lambda text: self._status(text, "Ok.TLabel", 5))
        self._apply_advanced(page)
        return page

    def _field_row(self, card: ttk.Frame, f: Field, *, first: bool) -> None:
        row = self._row(card, f.label, f.help, first=first)
        control = self._make_control(f, row.frame)
        if control.wide:
            control.widget.grid(row=4, column=0, columnspan=2, sticky="we",
                                pady=(self.theme.px(10), 0))
        else:
            control.widget.grid(row=0, column=1, rowspan=4, sticky="e",
                                padx=(self.theme.px(20), 0))
        self.controls[f.key] = control
        self.rows[f.key] = row
        control.recheck()  # flags a value from the file that can't be used (now it has a row)
        self._update_notes(f.key)
        if self.model.error is not None:
            control.set_enabled(False)

    def _make_control(self, f: Field, parent: ttk.Frame) -> Control:
        if f.kind == "device":
            options = [Option("", "Windows default")] + [
                Option(name, name) for name in self.system.microphones()]
            return ChoiceControl(self, f, parent, options)
        if f.kind == "language":
            options = [Option("en", "English (most accurate)"), Option("auto", "Detect it")]
            options += sorted((Option(code, f"{name} ({code})")
                               for code, name in LANGUAGE_NAMES.items() if code != "en"),
                              key=lambda o: o.label)
            return ChoiceControl(self, f, parent, options)
        if f.kind == "translate":
            options = [Option("", "Off")] + [Option(name, name)
                                             for name in sorted(LANGUAGE_NAMES.values())]
            return ChoiceControl(self, f, parent, options)
        if f.kind == "gpu":
            control = ChoiceControl(self, f, parent, self._gpu_options())
            if self.system.gpu_runtime() and self._gpus is None:
                self._find_gpus()
            return control
        return BUILDERS[f.kind](self, f, parent)

    def _gpu_options(self) -> list[Option]:
        options = [Option("off", "CPU only"), Option("auto", "Graphics card (automatic)")]
        return options + [Option(name, name) for name in self._gpus or []]

    def _find_gpus(self) -> None:
        self._gpus = []

        def work() -> None:
            try:
                found = self.system.gpus()
            except Exception:
                log.exception("listing GPUs failed")
                found = []
            with contextlib.suppress(RuntimeError, tk.TclError):  # window closed meanwhile
                self.root.after(0, lambda: self._gpus_found(found))

        import threading

        threading.Thread(target=work, name="gpus", daemon=True).start()

    def _gpus_found(self, names: list[str]) -> None:
        self._gpus = names
        control = self.controls.get("llm.gpu")
        if isinstance(control, ChoiceControl):
            control.set_options(self._gpu_options())

    def _autostart_row(self, card: ttk.Frame) -> None:
        row = self._row(card, "Start Keynone when I sign in",
                        "Adds a shortcut to your Startup folder - the one file Keynone writes "
                        "outside its own folder.", first=True)
        switch = ttk.Checkbutton(row.frame, variable=self.autostart, style="Switch.TCheckbutton",
                                 command=self._update_footer)
        switch.grid(row=0, column=1, rowspan=4, sticky="e", padx=(self.theme.px(20), 0))

    def _builtin_profiles(self, page: _Page) -> None:
        px = self.theme.px
        card = self._card(page, "Built in")
        body = ttk.Frame(card, padding=(px(16), px(10)))
        body.grid(row=0, column=0, sticky="we")
        body.columnconfigure(1, weight=1)
        for row, (name, text) in enumerate(builtin_profiles_text()):
            ttk.Label(body, text=name, style="Strong.TLabel").grid(
                row=row, column=0, sticky="nw", padx=(0, px(14)), pady=px(3))
            ttk.Label(body, text=text, style="Muted.TLabel", wraplength=px(520),
                      justify="left").grid(row=row, column=1, sticky="w", pady=px(3))

    def _apply_advanced(self, page: _Page | None = None) -> None:
        pages = [page] if page is not None else list(self.pages.values())
        for each in pages:
            for widget in each.advanced:
                if self.show_advanced.get():
                    widget.grid()
                else:
                    widget.grid_remove()

    # ----------------------------------------------------------------- home

    def _build_home(self) -> _Page:
        page = self._page("Home", "What Keynone is doing, your shortcuts, and how much you "
                                  "have dictated. All of it stays on this computer.")
        self.home = HomeView(page.frame, theme=self.theme, system=self.system,
                             setting=self.model.saved,  # what Keynone uses now
                             card=lambda title: self._card(page, title),
                             show_section=self.show_section)
        return page

    # ----------------------------------------------------------------- history

    def _build_history(self) -> _Page:
        page = self._page("History",
                          "Your recent dictations. Copy one to use it again; the paste-last "
                          "shortcut pastes the newest. How long they are kept is set in "
                          "Display & privacy.")

        def status(text: str) -> None:
            self._status(text, "Error.TLabel" if text.startswith("Couldn't") else "Ok.TLabel", 4)

        self.history = HistoryView(page.frame, theme=self.theme, system=self.system,
                                   enabled=lambda: bool(self.model.get("history.enabled")),
                                   show_privacy=lambda: self.show_section("privacy"),
                                   status=status)
        return page

    # ----------------------------------------------------------------- about

    def _build_about(self) -> _Page:
        px = self.theme.px
        page = self._page("About Keynone",
                          "Local, private voice dictation for Windows. Speech recognition and "
                          "the language model run on this computer; nothing you say leaves it.")
        card = self._card(page)
        edition = self.system.edition()
        self._info_row(card, "Version", __version__, first=True)
        row = self._info_row(card, "Edition", f"{edition.name}: {edition.summary}.")
        if edition is not LANGUAGES:
            extra = LANGUAGES.download_mb - edition.download_mb
            self._note(row, f"The language pack adds dictation in 40 languages and translation "
                            f"({extra} MB).", "Add the language pack",
                       lambda: self.system.run_in_terminal("setup", "--edition", "languages"))
        self._info_row(card, "Program folder", str(self.paths.home),
                       ("Open", lambda: self.system.open(self.paths.home)))
        self._info_row(card, "Settings file", str(self.paths.config_file),
                       ("Open", lambda: self.system.open(self.paths.config_file)))
        self._info_row(card, "Logs", str(self.paths.logs),
                       ("Open", lambda: self.system.open(self.paths.logs)))

        models = self._card(page, "Models")
        first = True
        for asset_id, role, name in (
                ("nemotron-en-560ms-int8", "Speech recognition", "NVIDIA Nemotron Speech EN 0.6B"),
                ("lfm2.5-1.2b-instruct-q4km", "Language model", "Liquid LFM2.5 1.2B Instruct"),
                ("qwen3.5-2b-q4km", "Language model (optional)", "Qwen3.5 2B"),
                (MULTILINGUAL_MODEL, "Language pack", "NVIDIA Nemotron 3.5 ASR, 40 locales"),
                (GPU_RUNTIME, "GPU runtime (optional)", "llama.cpp for Vulkan")):
            installed = self.system.installed(asset_id)
            action = None if installed else (
                "Install", lambda a=asset_id: self.system.run_in_terminal("setup", "--with", a))
            state = f"installed, {self.system.size_mb(asset_id)} MB" if installed else (
                "not installed")
            self._info_row(models, role, f"{name} · {state}", action, first=first)
            first = False

        tools = self._card(page, "Tools")
        self._info_row(tools, "Check the installation",
                       "Runs keynone doctor in a terminal window.",
                       ("Run", lambda: self.system.run_in_terminal("doctor")), first=True)
        self._info_row(tools, "Restore the default settings",
                       "Your current config.toml is kept as config.toml.bak.",
                       ("Restore", self.restore_defaults))
        ttk.Label(page.frame, style="PageMuted.TLabel", wraplength=px(600), justify="left",
                  text="Keynone is free and open source. The models keep their own licenses; "
                       "see README.md in the program folder.").grid(
            row=page.frame.grid_size()[1], column=0, sticky="w", pady=(px(18), 0))
        return page

    def _info_row(self, card: ttk.Frame, label: str, value: str,
                  action: tuple[str, Callable[[], None]] | None = None, *,
                  first: bool = False) -> _Row:
        row = self._row(card, label, "", first=first)
        ttk.Label(row.frame, text=value, style="Muted.TLabel", wraplength=self.theme.px(400),
                  justify="left").grid(row=1, column=0, sticky="w")
        if action is not None:
            ttk.Button(row.frame, text=action[0], command=action[1]).grid(
                row=0, column=1, rowspan=2, sticky="e", padx=(self.theme.px(20), 0))
        return row

    # ================================================================= notes

    def _note(self, row: _Row, text: str, button: str = "",
              action: Callable[[], None] | None = None) -> None:
        if not text:
            row.note.grid_remove()
            return
        row.note_text.configure(text=text)
        row.note_action[:] = [action] if action else []
        if button and action:
            row.note_button.configure(text=button)
            row.note_button.pack(side="left", padx=(self.theme.px(6), 0))
        else:
            row.note_button.pack_forget()
        row.note.grid(row=3, column=0, sticky="w", pady=(self.theme.px(3), 0))

    def _update_notes(self, key: str) -> None:
        """Hints that depend on what is installed (shown under the setting)."""
        row, control = self.rows.get(key), self.controls.get(key)
        if row is None or control is None:
            return
        f = control.field
        value = self.model.get(key)
        install = self.system.run_in_terminal
        if f.languages and not self.system.language_pack():
            control.set_enabled(False)
            self._note(row, "Part of the language pack, which isn't installed.",
                       "Add the language pack",
                       lambda: install("setup", "--edition", "languages"))
        elif key in ("asr.model", "llm.model") and not self.system.installed(str(value)):
            self._note(row, "This model isn't downloaded yet.", "Download it",
                       lambda: install("setup", "--with", str(self.model.get(key))))
        elif key == "llm.gpu" and not self.system.gpu_runtime():
            control.set_enabled(False)
            self._note(row, "Needs the GPU runtime (90 MB).", "Install it",
                       lambda: install("setup", "--with", GPU_RUNTIME))
        else:
            self._note(row, "")

    # ================================================================= model events

    def edited(self, key: str, value: Any) -> None:
        if key in self.controls and self.model.get(key) == value:
            self._update_footer()
            return
        self.model.set(key, value)
        if key in HOTKEY_KEYS:
            for other in HOTKEY_KEYS:  # e.g. dictation and edit shortcuts must differ
                if other != key and other in self.controls:
                    self.controls[other].recheck()
        self._update_notes(key)
        self._update_footer()

    def problem(self, key: str, message: str | None) -> None:
        if message:
            self.problems[key] = message
        else:
            self.problems.pop(key, None)
        row = self.rows.get(key)
        if row is not None:
            if message:
                row.problem.configure(text=message)
                row.problem.grid(row=2, column=0, sticky="w", pady=(self.theme.px(3), 0))
            else:
                row.problem.grid_remove()
        self._update_footer()

    # ================================================================= footer

    def _changes(self) -> int:
        return len(self.model.changed_keys()) + (self.autostart.get() != self.autostart_saved)

    def _update_footer(self) -> None:
        if not hasattr(self, "save_button"):
            return  # still building
        changes = self._changes()
        state = ["!disabled"] if changes and not self.problems else ["disabled"]
        self.save_button.state(state)
        self.revert_button.state(["!disabled"] if changes else ["disabled"])
        if self._status_job is not None:
            return  # a "Saved" message is showing
        if self.problems:
            self._status("Fix the highlighted setting to save.", "Error.TLabel")
        elif changes:
            self._status(f"{changes} unsaved change{'s' * (changes != 1)}", "Strong.TLabel")
        else:
            self._status("All changes saved" if self.model.error is None else "", "Muted.TLabel")

    def _status(self, text: str, style: str, seconds: float = 0) -> None:
        self.status.configure(text=text, style=style)
        if seconds:
            if self._status_job is not None:
                self.root.after_cancel(self._status_job)

            def clear() -> None:
                self._status_job = None
                self._update_footer()

            self._status_job = self.root.after(int(seconds * 1000), clear)

    def _update_sidebar(self) -> None:
        edition = self.system.edition()
        self.edition_label.configure(
            text=f"{__version__} · {'+ languages' if edition is LANGUAGES else 'English'}")
        running = self.system.listener_running()
        self.listener_label.configure(
            text="●  Running in the tray" if running else "○  Not running",
            foreground=self.theme.palette.ok if running else self.theme.palette.muted)

    # ================================================================= actions

    def save(self) -> None:
        if not self._changes() or self.problems:
            return
        try:
            result = self.model.save()
        except ConfigError as exc:
            self._status(f"Not saved: {exc}", "Error.TLabel", 8)
            return
        except OSError as exc:
            self._status(f"Couldn't write config.toml: {exc.strerror or exc}", "Error.TLabel", 8)
            return
        if self.autostart.get() != self.autostart_saved:
            try:
                self.system.set_autostart(self.autostart.get())
                self.autostart_saved = self.autostart.get()
            except Exception as exc:
                log.exception("autostart change failed")
                self._status(f"Settings saved, but the Startup shortcut failed: {exc}",
                             "Error.TLabel", 8)
                self._refresh_all()
                return
        running = self.system.settings_saved()
        self._refresh_all()
        message = ("Saved. Keynone uses the new settings from the next dictation."
                   if running else "Saved. Start Keynone to use the new settings.")
        if result.merged:
            message += " Edits made to config.toml elsewhere were kept."
        self._disk_notice_shown = False
        self._hide_banner()
        self._status(message, "Ok.TLabel", 6)
        self._update_footer()

    def revert(self) -> None:
        if self.model.changed_on_disk():
            self.model.load()  # as the banner says: the file as it is now
        else:
            self.model.revert()
        self.autostart.set(self.autostart_saved)
        self._disk_notice_shown = False
        self._hide_banner()
        self._refresh_all()
        self._status("Changes discarded.", "Muted.TLabel", 3)

    def restore_defaults(self) -> None:
        if not messagebox.askyesno(
                TITLE, "Replace your settings with the defaults?\n\nYour current config.toml "
                       "is kept as config.toml.bak.", parent=self.root, icon="warning"):
            return
        backup = self.model.restore_defaults()
        self.system.settings_saved()
        self._refresh_all()
        self._status(f"Default settings restored. The old file is {backup.name}.",
                     "Ok.TLabel", 6)

    def _refresh_all(self) -> None:
        self.problems.clear()
        for row in self.rows.values():
            row.problem.grid_remove()
        for key, control in self.controls.items():
            control.refresh()  # may flag a value from the file again (e.g. a bad shortcut)
            control.set_enabled(self.model.error is None)
            self._update_notes(key)
        if self.home is not None:
            self.home.refresh()  # e.g. a new shortcut was saved
        self._show_file_error()
        self._update_footer()

    def close(self) -> None:
        if self._changes():
            answer = messagebox.askyesnocancel(TITLE, "Save your changes before closing?",
                                               parent=self.root)
            if answer is None:
                return
            if answer:
                self.save()
                if self._changes():
                    return  # saving failed: the footer says why
        for view in (self.history, self.home):
            if view is not None:
                view.close()
        self.root.destroy()

    # ================================================================= the file on disk

    def _watch(self) -> None:
        """config.toml edited in another program, and the listener's state."""
        try:
            if self.model.changed_on_disk():
                if self._changes() == 0 or self.model.error is not None:
                    self.model.load()
                    self._refresh_all()
                    self._status("config.toml changed on disk - reloaded.", "Muted.TLabel", 4)
                elif not self._disk_notice_shown:
                    self._disk_notice_shown = True
                    self._banner("config.toml was changed by another program. Saving merges "
                                 "your changes into it; Revert loads it as it is now.")
            self._update_sidebar()
            view = self._live_view()
            if view is not None:
                view.poll()  # a dictation made meanwhile shows up
        except Exception:
            log.exception("watching config.toml failed")
        self.root.after(1500, self._watch)

    def _show_file_error(self) -> None:
        if self.model.error is None:
            if not self._disk_notice_shown:
                self._hide_banner()
            return
        self._banner(f"config.toml can't be used as it is: {self.model.error}\nFix it in a text "
                     "editor (this window reloads it), or restore the defaults.",
                     [("Open config.toml", lambda: self.system.open(self.paths.config_file)),
                      ("Restore defaults", self.restore_defaults)])

    def _banner(self, text: str,
                buttons: list[tuple[str, Callable[[], None]]] | None = None) -> None:
        for child in self.banner_buttons.winfo_children():
            child.destroy()
        for label, action in buttons or []:
            ttk.Button(self.banner_buttons, text=label, command=action).pack(
                side="left", padx=(self.theme.px(6), 0), pady=self.theme.px(8))
        self.banner_text.configure(text=text)
        self.banner.pack(side="top", fill="x", before=self.scroll)

    def _hide_banner(self) -> None:
        self.banner.pack_forget()


# --------------------------------------------------------------------------- entry point

_MUTEX_NAME = "Local\\Keynone.Settings"


def _bring_existing_to_front() -> bool:
    user32 = ctypes.windll.user32
    hwnd = user32.FindWindowW(None, TITLE)
    if not hwnd:
        return False
    user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    user32.SetForegroundWindow(hwnd)
    return True


def _new_tk(attempts: int = 3) -> tk.Tk:
    """tk.Tk(), retried: Tcl's start-up occasionally fails once on Windows
    ('invalid command name "tcl_findLibrary"') and works on the next try."""
    for attempt in range(attempts):
        try:
            return tk.Tk()
        except tk.TclError:
            if attempt == attempts - 1:
                raise
            log.warning("Tk did not start; trying again", exc_info=True)
    raise AssertionError("unreachable")


def run_settings(paths: Paths | None = None) -> int:
    from keynone.logging_setup import setup_logging
    from keynone.win32 import api
    from keynone.win32.gui import BRAND_RGB, write_icon

    paths = paths or get_paths()
    paths.ensure()
    setup_logging("settings", paths=paths)
    mutex = api.CreateMutexW(None, False, _MUTEX_NAME)
    if ctypes.get_last_error() == api.ERROR_ALREADY_EXISTS:
        _bring_existing_to_front()
        api.CloseHandle(mutex)
        return 0
    try:
        with contextlib.suppress(AttributeError, OSError):
            ctypes.windll.shcore.SetProcessDpiAwareness(1)  # crisp text at 125 % / 150 %
        with contextlib.suppress(AttributeError, OSError):
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Keynone.Settings")
        root = _new_tk()
        root.withdraw()
        icon = write_icon(paths.cache / "icons" / "idle.ico", BRAND_RGB)
        with contextlib.suppress(tk.TclError):
            root.iconbitmap(default=str(icon))
        app = SettingsApp(root, paths)
        root.update_idletasks()
        width, height = root.winfo_reqwidth(), root.winfo_reqheight()
        width = max(width, root.winfo_width())
        height = max(height, root.winfo_height())
        x = (root.winfo_screenwidth() - width) // 2
        y = max(0, (root.winfo_screenheight() - height) // 2 - app.theme.px(20))
        root.geometry(f"+{x}+{y}")
        style_title_bar(root, app.theme.palette.dark)
        root.deiconify()
        root.lift()
        root.focus_force()
        root.mainloop()
    finally:
        api.CloseHandle(mutex)
    return 0
