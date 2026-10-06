"""How the settings window looks.

Plain Tk/ttk on the "clam" theme, recoloured. A light and a dark palette follow
the Windows app theme; text is Segoe UI. Switches, rounded fields and buttons,
and chevrons are small images drawn with anti-aliasing at start-up, at the
screen's scaling. No image files, no third-party packages.
"""

from __future__ import annotations

import base64
import contextlib
import ctypes
import itertools
import math
import struct
import tkinter as tk
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from tkinter import font as tkfont
from tkinter import ttk
from typing import Any

RGB = tuple[int, int, int]
Layout = list[tuple[str, dict[str, Any]]]  # a ttk layout, as tkinter takes it


@dataclass(frozen=True)
class Palette:
    dark: bool
    bg: str  # content area
    sidebar: str
    surface: str  # cards, fields, footer
    hover: str  # sidebar item under the mouse
    selected: str  # current sidebar item
    border: str
    border_strong: str
    text: str
    muted: str
    faint: str  # disabled text, placeholders
    accent: str
    accent_hover: str
    accent_pressed: str
    on_accent: str  # text on an accent background
    danger: str
    ok: str
    note_bg: str  # banners
    note_text: str


LIGHT = Palette(
    dark=False, bg="#f6f6f4", sidebar="#ededea", surface="#ffffff", hover="#e4e4e0",
    selected="#dddcd7", border="#d9d9d4", border_strong="#b3b3ad", text="#1d1d1b",
    muted="#65655f", faint="#a3a39d", accent="#3a64b0", accent_hover="#30589f",
    accent_pressed="#284b89", on_accent="#ffffff", danger="#c2362e", ok="#2d7a4c",
    note_bg="#f8f0d8", note_text="#5c4813",
)
DARK = Palette(
    dark=True, bg="#1e1e1e", sidebar="#181818", surface="#272727", hover="#262626",
    selected="#2f2f2f", border="#363636", border_strong="#5a5a5a", text="#e7e7e4",
    muted="#a5a5a0", faint="#6a6a66", accent="#7ea6ea", accent_hover="#93b5ef",
    accent_pressed="#6b93d9", on_accent="#0f1420", danger="#f0756c", ok="#73c592",
    note_bg="#3b3322", note_text="#ecdcaf",
)


def windows_prefers_dark() -> bool:
    """The "app mode" chosen in Windows Settings > Personalization > Colors."""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
    except OSError:
        return False
    return value == 0


def style_title_bar(window: tk.Misc, dark: bool) -> None:
    """Ask Windows 10 (20H1+) / 11 for a title bar that matches the palette."""
    with contextlib.suppress(AttributeError, OSError):
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        value = ctypes.c_int(1 if dark else 0)
        for attribute in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (newer, older builds)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value)) == 0:
                break


# --------------------------------------------------------------------------- drawing


def _rgb(color: str) -> RGB:
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def _png(width: int, height: int, rgba: bytearray) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    stride = width * 4
    raw = b"".join(b"\x00" + bytes(rgba[y * stride:(y + 1) * stride]) for y in range(height))
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))


Shape = Callable[[float, float], float]  # signed distance: < 0 inside, in pixels


def rounded_rect(x0: float, y0: float, x1: float, y1: float, radius: float) -> Shape:
    cx, cy, hw, hh = (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) / 2, (y1 - y0) / 2
    radius = max(0.0, min(radius, hw, hh))

    def distance(x: float, y: float) -> float:
        qx, qy = abs(x - cx) - (hw - radius), abs(y - cy) - (hh - radius)
        return math.hypot(max(qx, 0.0), max(qy, 0.0)) + min(max(qx, qy), 0.0) - radius

    return distance


def circle(cx: float, cy: float, radius: float) -> Shape:
    return lambda x, y: math.hypot(x - cx, y - cy) - radius


def polyline(points: list[tuple[float, float]], width: float) -> Shape:
    def distance(x: float, y: float) -> float:
        best = math.inf
        for (ax, ay), (bx, by) in itertools.pairwise(points):
            dx, dy = bx - ax, by - ay
            t = max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / (dx * dx + dy * dy)))
            best = min(best, math.hypot(x - ax - t * dx, y - ay - t * dy))
        return best - width / 2

    return distance


def draw(width: int, height: int, layers: list[tuple[Shape, str]]) -> bytes:
    """PNG of the layers painted bottom to top, anti-aliased (coverage from distance)."""
    colors = [_rgb(color) for _, color in layers]
    rgba = bytearray(width * height * 4)
    for y in range(height):
        for x in range(width):
            px, py = x + 0.5, y + 0.5
            r = g = b = a = 0.0  # premultiplied
            for (shape, _), (cr, cg, cb) in zip(layers, colors, strict=True):
                cover = min(1.0, max(0.0, 0.5 - shape(px, py)))
                if cover:
                    r, g, b = (cr * cover + r * (1 - cover), cg * cover + g * (1 - cover),
                               cb * cover + b * (1 - cover))
                    a = cover + a * (1 - cover)
            if a:
                i = (y * width + x) * 4
                rgba[i:i + 4] = bytes((round(r / a), round(g / a), round(b / a), round(a * 255)))
    return _png(width, height, rgba)


# --------------------------------------------------------------------------- theme


class _Style(ttk.Style):
    def element_create(self, elementname: str, etype: str, *args: Any, **kw: Any) -> None:
        # ttk elements live as long as the Tcl interpreter. A second window in the
        # same process keeps them: its images replace the first one's by name.
        if elementname not in self.element_names():
            super().element_create(elementname, etype, *args, **kw)  # type: ignore[call-overload]

    def set_layout(self, name: str, layout: Layout) -> None:
        self.layout(name, layout)  # type: ignore[arg-type]  # the stub is narrower than ttk


@dataclass
class Fonts:
    body: tkfont.Font
    small: tkfont.Font
    strong: tkfont.Font
    title: tkfont.Font
    brand: tkfont.Font
    mono: tkfont.Font


class Theme:
    """Palette, fonts, scaling and the ttk styles of one window."""

    FRAME_BORDER = 6  # corner size of the rounded boxes, in design pixels

    def __init__(self, root: tk.Tk, palette: Palette | None = None) -> None:
        self.root = root
        self.palette = palette or (DARK if windows_prefers_dark() else LIGHT)
        self.scale = max(1.0, root.winfo_fpixels("1i") / 96.0)
        self._images: dict[str, tk.PhotoImage] = {}
        families = set(tkfont.families(root))
        ui = "Segoe UI" if "Segoe UI" in families else "TkDefaultFont"
        semibold = "Segoe UI Semibold" if "Segoe UI Semibold" in families else ui
        mono = next((f for f in ("Cascadia Mono", "Consolas") if f in families), "TkFixedFont")
        self.fonts = Fonts(
            body=tkfont.Font(root, family=ui, size=10),
            small=tkfont.Font(root, family=ui, size=9),
            strong=tkfont.Font(root, family=semibold, size=10),
            title=tkfont.Font(root, family=semibold, size=15),
            brand=tkfont.Font(root, family=semibold, size=12),
            mono=tkfont.Font(root, family=mono, size=10),
        )
        self._configure()

    def px(self, value: float) -> int:
        """Design pixels (at 100 % scaling) to screen pixels."""
        return round(value * self.scale)

    def image(self, name: str) -> tk.PhotoImage:
        return self._images[name]

    def _add(self, name: str, width: float, height: float,
             layers: Callable[[float], list[tuple[Shape, str]]]) -> str:
        """Draw an image at design size ``width`` x ``height``; ``layers(s)`` gets the scale."""
        w, h = self.px(width), self.px(height)
        data = draw(w, h, layers(w / width))
        self._images[name] = tk.PhotoImage(master=self.root, name=f"kn_{name}",
                                           data=base64.b64encode(data).decode())
        return f"kn_{name}"

    # ----------------------------------------------------------------- images

    def _frame_image(self, name: str, fill: str, edge: str, edge_width: float = 1.0,
                     radius: float = 5.0, backdrop: str | None = None) -> str:
        """A rounded box for a 9-slice ttk element (corners ``FRAME_BORDER`` wide).

        Drawn small, then stretched inside Tk to a large image: ttk tiles an
        element's middle part to the widget's size, and tiling a few-pixel middle
        with alpha costs ~50 ms per widget on Windows. A wide middle costs ~1 ms.

        ``backdrop`` paints the corners instead of leaving them transparent: Tk
        draws the half-transparent pixels of *disabled*-state images too light."""
        size = 2 * radius + 6

        def layers(s: float) -> list[tuple[Shape, str]]:
            w = size * s
            outer = rounded_rect(0, 0, w, w, radius * s)
            inset = edge_width * s
            inner = rounded_rect(inset, inset, w - inset, w - inset, max(0.0, radius * s - inset))
            under = [(rounded_rect(-1, -1, w + 1, w + 1, 0), backdrop)] if backdrop else []
            return [*under, (outer, edge), (inner, fill)]

        self._add(f"{name}_small", size, size, layers)
        small = self._images[f"{name}_small"]
        b = self.px(self.FRAME_BORDER)
        width, height = self.px(220), self.px(56)
        big = tk.PhotoImage(master=self.root, name=f"kn_{name}", width=width, height=height)
        w0, h0 = small.width(), small.height()
        spans_x = ((0, b, 0, b), (b, w0 - b, b, width - b), (w0 - b, w0, width - b, width))
        spans_y = ((0, b, 0, b), (b, h0 - b, b, height - b), (h0 - b, h0, height - b, height))
        for sx0, sx1, dx0, dx1 in spans_x:
            for sy0, sy1, dy0, dy1 in spans_y:
                big.tk.call(str(big), "copy", str(small), "-from", sx0, sy0, sx1, sy1,
                            "-to", dx0, dy0, dx1, dy1)
        self._images[name] = big
        return str(big)

    def _switch_image(self, name: str, on: bool, disabled: bool = False, focus: bool = False,
                      hover: bool = False) -> str:
        p = self.palette
        width, height = 38.0, 20.0
        pad = 2.0  # room for the focus ring

        if on:
            track = p.border_strong if disabled else (p.accent_hover if hover else p.accent)
            knob, edge = (p.surface if disabled else p.on_accent), track
        else:
            track, edge = p.surface, (p.border if disabled else
                                      (p.muted if hover else p.border_strong))
            knob = p.faint if disabled else (p.text if hover else p.muted)

        def layers(s: float) -> list[tuple[Shape, str]]:
            w, h, q = width * s, height * s, pad * s
            out: list[tuple[Shape, str]] = []
            if disabled:  # opaque: see _frame_image's backdrop
                out.append((rounded_rect(-1, -1, w + 1, h + 1, 0), p.surface))
            if focus:
                out.append((rounded_rect(0, 0, w, h, h / 2), p.accent))
                out.append((rounded_rect(1.2 * s, 1.2 * s, w - 1.2 * s, h - 1.2 * s, h / 2),
                            p.surface))
            out.append((rounded_rect(q, q, w - q, h - q, h / 2), edge))
            line = 1.2 * s
            out.append((rounded_rect(q + line, q + line, w - q - line, h - q - line, h / 2),
                        track))
            knob_r = (h - 2 * q) / 2 - 3.2 * s
            cx = w - q - (h - 2 * q) / 2 if on else q + (h - 2 * q) / 2
            out.append((circle(cx, h / 2, knob_r), knob))
            return out

        return self._add(name, width, height, layers)

    def _chevron(self, name: str, color: str, up: bool = False, width: float = 18.0,
                 height: float = 14.0, half: float = 3.6) -> str:

        def layers(s: float) -> list[tuple[Shape, str]]:
            cx, cy, h, rise = width / 2 * s, height / 2 * s, half * s, half * 0.53 * s
            sign = -1 if up else 1
            points = [(cx - h, cy - sign * rise), (cx, cy + sign * rise),
                      (cx + h, cy - sign * rise)]
            return [(polyline(points, 1.5 * s), color)]

        return self._add(name, width, height, layers)

    def brand_image(self, size: float = 24.0) -> tk.PhotoImage:
        """The tray icon's microphone badge, for the sidebar."""
        badge = "#545b7a" if self.palette.dark else "#3b4057"

        def layers(s: float) -> list[tuple[Shape, str]]:
            k = s * size / 32  # the glyph is designed on a 32 x 32 grid, like the tray icon
            arc = [(16 + 7 * math.cos(math.pi * i / 12), 15 + 7 * math.sin(math.pi * i / 12))
                   for i in range(13)]
            return [
                (circle(16 * k, 16 * k, 15.5 * k), badge),
                (rounded_rect(12 * k, 7 * k, 20 * k, 18 * k, 4 * k), "#ffffff"),
                (polyline([(x * k, y * k) for x, y in arc], 1.6 * k), "#ffffff"),
                (rounded_rect(15.1 * k, 21.5 * k, 16.9 * k, 25.5 * k, 0.3 * k), "#ffffff"),
                (rounded_rect(12 * k, 25 * k, 20 * k, 26.6 * k, 0.8 * k), "#ffffff"),
            ]

        self._add("brand", size, size, layers)
        return self._images["brand"]

    def _thumb(self, name: str, color: str) -> str:
        width, height = 10.0, 30.0

        def layers(s: float) -> list[tuple[Shape, str]]:
            return [(rounded_rect(2.5 * s, 1 * s, 7.5 * s, 29 * s, 2.5 * s), color)]

        return self._add(name, width, height, layers)

    # ----------------------------------------------------------------- styles

    def _configure(self) -> None:
        p, f, root = self.palette, self.fonts, self.root
        style = _Style(root)
        style.theme_use("clam")
        root.configure(background=p.bg)
        style.configure(".", background=p.surface, foreground=p.text, font=f.body,
                        bordercolor=p.border, lightcolor=p.surface, darkcolor=p.surface,
                        troughcolor=p.bg, focuscolor=p.accent, selectbackground=p.accent,
                        selectforeground=p.on_accent, insertcolor=p.text,
                        fieldbackground=p.surface, insertwidth=max(1, self.px(1)))
        style.map(".", foreground=[("disabled", p.faint)])

        # labels: on cards (default), on the content background, in the sidebar
        style.configure("TLabel", background=p.surface, foreground=p.text)
        style.configure("Muted.TLabel", foreground=p.muted, font=f.small)
        style.configure("Strong.TLabel", font=f.strong)
        style.configure("Mono.TLabel", font=f.mono)
        style.configure("Error.TLabel", foreground=p.danger, font=f.small)
        style.configure("Ok.TLabel", foreground=p.ok, font=f.small)
        style.configure("Page.TLabel", background=p.bg)
        style.configure("PageTitle.TLabel", background=p.bg, font=f.title)
        style.configure("PageMuted.TLabel", background=p.bg, foreground=p.muted)
        style.configure("Group.TLabel", background=p.bg, foreground=p.muted, font=f.strong)
        style.configure("Note.TLabel", background=p.note_bg, foreground=p.note_text)
        style.configure("TFrame", background=p.surface)
        style.configure("Page.TFrame", background=p.bg)
        style.configure("Note.TFrame", background=p.note_bg)

        # buttons
        b = self.px(self.FRAME_BORDER)
        normal = self._frame_image("btn", p.surface, p.border_strong)
        hover = self._frame_image("btn_hover", p.hover if p.dark else p.bg, p.muted)
        pressed = self._frame_image("btn_pressed", p.selected, p.muted)
        focus = self._frame_image("btn_focus", p.surface, p.accent, 1.6)
        disabled = self._frame_image("btn_disabled", p.surface, p.border, backdrop=p.surface)
        # The images are large (see _frame_image); width/height keep widgets small.
        box: dict[str, Any] = {"border": b, "width": 2 * b, "height": 2 * b, "sticky": "nsew"}
        style.element_create("Kn.Button.border", "image", normal,
                             ("disabled", disabled), ("pressed", pressed), ("active", hover),
                             ("focus", focus), padding=self.px(1), **box)
        layout: Layout = [("Kn.Button.border", {"sticky": "nsew", "children": [
            ("Button.padding", {"sticky": "nsew", "children": [
                ("Button.label", {"sticky": "nsew"})]})]})]
        style.set_layout("TButton", layout)
        style.configure("TButton", padding=(self.px(12), self.px(4)), anchor="center")
        accent = self._frame_image("acc", p.accent, p.accent)
        accent_hover = self._frame_image("acc_hover", p.accent_hover, p.accent_hover)
        accent_pressed = self._frame_image("acc_pressed", p.accent_pressed, p.accent_pressed)
        accent_focus = self._frame_image("acc_focus", p.accent, p.text, 1.6)
        accent_disabled = self._frame_image("acc_disabled", p.border, p.border,
                                            backdrop=p.surface)
        style.element_create("Kn.Accent.border", "image", accent,
                             ("disabled", accent_disabled), ("pressed", accent_pressed),
                             ("active", accent_hover), ("focus", accent_focus),
                             padding=self.px(1), **box)
        style.set_layout("Accent.TButton", [("Kn.Accent.border", layout[0][1])])
        style.configure("Accent.TButton", foreground=p.on_accent, font=f.strong,
                        padding=(self.px(16), self.px(4)))
        style.map("Accent.TButton", foreground=[("disabled", p.faint)])
        # a button that reads as a link: actions that open something
        style.set_layout("Link.TButton", [("Button.padding", {"sticky": "nsew", "children": [
            ("Button.label", {"sticky": "nsew"})]})])
        style.configure("Link.TButton", foreground=p.accent, padding=(self.px(2), self.px(2)))
        style.map("Link.TButton", foreground=[("disabled", p.faint), ("active", p.accent_hover)],
                  font=[("active", f.body)])

        # fields: entry, combobox, spinbox share one look
        field = self._frame_image("field", p.surface, p.border_strong)
        field_hover = self._frame_image("field_hover", p.surface, p.muted)
        field_focus = self._frame_image("field_focus", p.surface, p.accent, 1.6)
        field_bad = self._frame_image("field_bad", p.surface, p.danger, 1.6)
        field_off = self._frame_image("field_off", p.bg, p.border, backdrop=p.surface)
        states = (("disabled", field_off), ("invalid", field_bad), ("focus", field_focus),
                  ("active", field_hover))
        pad = (self.px(8), self.px(4))
        for widget in ("Entry", "Combobox", "Spinbox"):
            style.element_create(f"Kn.{widget}.field", "image", field, *states, padding=pad,
                                 **box)
        style.set_layout("TEntry", [("Kn.Entry.field", {"sticky": "nsew", "children": [
            ("Entry.padding", {"sticky": "nsew", "children": [
                ("Entry.textarea", {"sticky": "nsew"})]})]})])
        down = self._chevron("down", p.muted)
        down_off = self._chevron("down_off", p.faint)

        def spin_chevron(name: str, color: str, up: bool) -> str:
            return self._chevron(name, color, up=up, width=16.0, height=9.0, half=3.2)

        spin_up = spin_chevron("spin_up", p.muted, True)
        spin_up_off = spin_chevron("spin_up_off", p.faint, True)
        spin_down = spin_chevron("spin_down", p.muted, False)
        spin_down_off = spin_chevron("spin_down_off", p.faint, False)
        style.element_create("Kn.Combobox.downarrow", "image", down, ("disabled", down_off),
                             sticky="")
        style.set_layout("TCombobox", [("Kn.Combobox.field", {"sticky": "nsew", "children": [
            ("Kn.Combobox.downarrow", {"side": "right", "sticky": "ns"}),
            ("Combobox.padding", {"expand": "1", "sticky": "nsew", "children": [
                ("Combobox.textarea", {"sticky": "nsew"})]})]})])
        style.configure("TCombobox", padding=(0, 0), arrowsize=self.px(12))
        style.map("TCombobox", fieldbackground=[("readonly", p.surface)],
                  selectbackground=[("readonly", p.surface), ("!focus", p.surface)],
                  selectforeground=[("readonly", p.text), ("!focus", p.text)],
                  foreground=[("disabled", p.faint)])
        style.element_create("Kn.Spinbox.uparrow", "image", spin_up, ("disabled", spin_up_off),
                             sticky="")
        style.element_create("Kn.Spinbox.downarrow", "image", spin_down,
                             ("disabled", spin_down_off), sticky="")
        style.set_layout("TSpinbox", [("Kn.Spinbox.field", {"sticky": "nsew", "children": [
            ("null", {"side": "right", "sticky": "ns", "children": [
                ("Kn.Spinbox.uparrow", {"side": "top", "sticky": "e"}),
                ("Kn.Spinbox.downarrow", {"side": "bottom", "sticky": "e"})]}),
            ("Spinbox.padding", {"sticky": "nsew", "children": [
                ("Spinbox.textarea", {"sticky": "nsew"})]})]})])
        style.configure("TSpinbox", padding=(0, 0), arrowsize=self.px(10))
        style.configure("Mono.TEntry", font=f.mono)
        style.configure("Mono.TCombobox", font=f.mono)
        root.option_add("*TCombobox*Listbox.background", p.surface)
        root.option_add("*TCombobox*Listbox.foreground", p.text)
        root.option_add("*TCombobox*Listbox.selectBackground", p.accent)
        root.option_add("*TCombobox*Listbox.selectForeground", p.on_accent)
        root.option_add("*TCombobox*Listbox.font", f.body)
        root.option_add("*TCombobox*Listbox.borderWidth", 0)
        root.option_add("*TCombobox*Listbox.relief", "flat")
        style.configure("ComboboxPopdownFrame", background=p.surface, bordercolor=p.border_strong,
                        lightcolor=p.surface, darkcolor=p.surface, relief="solid", borderwidth=1)

        # switch (a checkbutton with an image indicator)
        images = {
            "on": self._switch_image("sw_on", True),
            "on_hover": self._switch_image("sw_on_hover", True, hover=True),
            "on_focus": self._switch_image("sw_on_focus", True, focus=True),
            "on_off": self._switch_image("sw_on_disabled", True, disabled=True),
            "off": self._switch_image("sw_off", False),
            "off_hover": self._switch_image("sw_off_hover", False, hover=True),
            "off_focus": self._switch_image("sw_off_focus", False, focus=True),
            "off_off": self._switch_image("sw_off_disabled", False, disabled=True),
        }
        style.element_create(
            "Kn.Switch.indicator", "image", images["off"],
            ("disabled", "selected", images["on_off"]), ("disabled", images["off_off"]),
            ("selected", "focus", images["on_focus"]), ("selected", "active", images["on_hover"]),
            ("selected", images["on"]), ("focus", images["off_focus"]),
            ("active", images["off_hover"]), sticky="")
        style.set_layout("Switch.TCheckbutton", [("Checkbutton.padding", {
            "sticky": "nsew", "children": [
                ("Kn.Switch.indicator", {"side": "left", "sticky": ""}),
                ("Checkbutton.label", {"side": "left", "sticky": "w"})]})])
        style.configure("Switch.TCheckbutton", padding=(0, self.px(2)))
        style.configure("Sidebar.Switch.TCheckbutton", background=p.sidebar, foreground=p.muted,
                        font=f.small)

        # segmented choice (radio buttons that look like a row of chips)
        seg = self._frame_image("seg", p.surface, p.border_strong)
        seg_hover = self._frame_image("seg_hover", p.hover if p.dark else p.bg, p.muted)
        seg_on = self._frame_image("seg_on", p.accent, p.accent)
        seg_focus = self._frame_image("seg_focus", p.surface, p.accent, 1.6)
        seg_on_focus = self._frame_image("seg_on_focus", p.accent, p.text, 1.6)
        style.element_create("Kn.Segment.border", "image", seg,
                             ("disabled", field_off), ("selected", "focus", seg_on_focus),
                             ("selected", seg_on), ("focus", seg_focus), ("active", seg_hover),
                             padding=self.px(1), **box)
        style.set_layout("Segment.TRadiobutton", [("Kn.Segment.border", {
            "sticky": "nsew", "children": [
                ("Radiobutton.padding", {"sticky": "nsew", "children": [
                    ("Radiobutton.label", {"sticky": "nsew"})]})]})])
        style.configure("Segment.TRadiobutton", padding=(self.px(12), self.px(4)), anchor="center")
        style.map("Segment.TRadiobutton",
                  foreground=[("disabled", p.faint), ("selected", p.on_accent)])

        # slim scrollbar without arrows
        thumb = self._thumb("thumb", p.border_strong)
        thumb_hover = self._thumb("thumb_hover", p.muted)
        style.element_create("Kn.Vertical.Scrollbar.thumb", "image", thumb,
                             ("pressed", thumb_hover), ("active", thumb_hover),
                             border=(0, self.px(4)), sticky="ns")
        style.set_layout("Kn.Vertical.TScrollbar", [("Vertical.Scrollbar.trough", {
            "sticky": "ns", "children": [
                ("Kn.Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]})])
        style.configure("Kn.Vertical.TScrollbar", troughcolor=p.bg, background=p.bg,
                        bordercolor=p.bg, lightcolor=p.bg, darkcolor=p.bg, gripcount=0)

        style.configure("TSeparator", background=p.border)
        style.configure("Horizontal.TScale", background=p.accent, troughcolor=p.border,
                        bordercolor=p.surface, lightcolor=p.accent, darkcolor=p.accent)
