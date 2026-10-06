"""What the settings window shows: every option of ``data/config.toml`` with a
label, a short explanation and the kind of control it needs.

The file stays the single source of truth - the window only reads and writes
it - so this module adds presentation only. ``tests/test_settings_schema.py``
checks that every option of :class:`keynone.config.Config` appears here exactly
once, with limits the config accepts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Kind = Literal[
    "bool", "int", "float", "text", "choice", "hotkey", "device", "words", "replacements",
    "apps", "language", "translate", "gpu", "percent",
]


@dataclass(frozen=True)
class Option:
    """One value of a choice: what is stored and what the user reads."""

    value: object
    label: str


@dataclass(frozen=True)
class Field:
    key: str  # dotted path in config.toml, e.g. "hotkeys.dictate"
    label: str
    help: str = ""
    kind: Kind = "text"
    options: tuple[Option, ...] = ()  # "choice"; presets for "hotkey"
    minimum: float | None = None
    maximum: float | None = None
    step: float = 1
    unit: str = ""
    advanced: bool = False  # behind "Show advanced settings"
    languages: bool = False  # needs the language pack (multilingual speech model)


@dataclass(frozen=True)
class Section:
    id: str
    title: str
    description: str
    fields: tuple[Field, ...] = field(default_factory=tuple)


_HOTKEY_PRESETS = tuple(Option(v, v) for v in (
    "ctrl+win", "rctrl", "capslock", "f9", "ctrl+alt+space", "ctrl+shift+d"))
_EDIT_PRESETS = tuple(Option(v, v) for v in ("ctrl+win+alt", "ctrl+alt+e", "rctrl+rshift", ""))
_LLM_MODES = (
    Option("auto", "When needed (recommended)"),
    Option("always", "Every dictation"),
    Option("off", "Never - rules only"),
)
_STYLE_HELP = ("Programs are process names such as slack.exe. The chat style leaves out the "
               "final period; the code style adds no closing period and leaves capitals alone.")

SECTIONS: tuple[Section, ...] = (
    Section(
        "general", "General",
        "Hotkeys, and how long Keynone keeps its speech engine in memory.",
        (
            Field("hotkeys.dictate", "Dictation hotkey",
                  "Hold to dictate, release to insert. Double-tap for hands-free, tap again "
                  "to finish.", "hotkey", _HOTKEY_PRESETS),
            Field("hotkeys.edit", "Edit hotkey",
                  "Select text, hold this and say how to change it. Leave empty to turn "
                  "speak-to-edit off.", "hotkey", _EDIT_PRESETS),
            Field("hotkeys.cancel", "Cancel key", "Discards the dictation in progress.",
                  "hotkey", (Option("esc", "esc"),)),
            Field("lifecycle.keep_warm_seconds", "Keep the engine loaded for",
                  "After a dictation the engine stays in memory (idle, about 2 GB) so the "
                  "next one starts at once. 0 unloads it right away: nothing heavy keeps "
                  "running, but every dictation waits about 5 s for the model.",
                  "int", minimum=0, maximum=86_400, step=60, unit="seconds"),
            Field("hotkeys.hold_threshold_ms", "Tap length",
                  "A press shorter than this is a tap (used for double-tap).", "int",
                  minimum=100, maximum=1500, step=50, unit="ms", advanced=True),
            Field("hotkeys.double_tap_ms", "Double-tap window",
                  "Time allowed between the two taps of a double-tap.", "int",
                  minimum=150, maximum=1500, step=50, unit="ms", advanced=True),
        ),
    ),
    Section(
        "speech", "Microphone & speech",
        "Where your voice comes from and how it is recognized.",
        (
            Field("audio.device", "Microphone",
                  "Windows default follows the input device chosen in Windows settings.",
                  "device"),
            Field("audio.normalize_level", "Boost a quiet microphone",
                  "Lifts speech that is too quiet to be recognized well (a distant laptop "
                  "microphone). Normal levels are left alone.", "bool"),
            Field("audio.cues", "Start and stop sounds", "Short clicks when recording starts "
                  "and ends.", "bool"),
            Field("asr.language", "Language",
                  "English uses the most accurate English model. Other languages, or "
                  "detecting the language, use the multilingual model (language pack).",
                  "language", languages=True),
            Field("audio.max_session_seconds", "Longest dictation",
                  "A dictation stops by itself after this long.", "int",
                  minimum=5, maximum=7200, step=30, unit="seconds", advanced=True),
            Field("audio.handsfree_silence_stop_seconds", "Hands-free stops after silence of",
                  "0 = never; tap the hotkey to finish.", "float",
                  minimum=0, maximum=60, step=0.5, unit="seconds", advanced=True),
            Field("asr.model", "English speech model",
                  "The 160 ms model shows text sooner but needs a faster CPU.", "choice",
                  (Option("nemotron-en-560ms-int8", "Nemotron 0.6B, 560 ms (recommended)"),
                   Option("nemotron-en-160ms-int8", "Nemotron 0.6B, 160 ms (fast CPUs)")),
                  advanced=True),
            Field("asr.threads", "Speech recognition threads", "CPU threads for recognition.",
                  "int", minimum=1, maximum=32, advanced=True),
            Field("asr.endpoint_silence_seconds", "Pause that ends a sentence",
                  "A pause this long lets Keynone polish the sentence while you keep talking.",
                  "float", minimum=0.2, maximum=5, step=0.1, unit="seconds", advanced=True),
        ),
    ),
    Section(
        "cleanup", "Cleanup",
        "What happens to your words before they are typed. All of it runs on this computer.",
        (
            Field("cleanup.remove_fillers", "Remove filler words",
                  "um, uh, er, and \"you know\" or \"like\" between commas.", "bool"),
            Field("cleanup.voice_commands", "Voice commands",
                  "\"new line\", \"new paragraph\" and \"scratch that\".", "bool"),
            Field("cleanup.llm_mode", "Polish with the local AI",
                  "The local language model fixes what the rules can't (corrections like "
                  "\"just Jordan\", soft fillers, run-on sentences). \"When needed\" keeps "
                  "most dictations instant.", "choice", _LLM_MODES),
            Field("cleanup.translate_to", "Translate every dictation to",
                  "Writes everything you dictate in this language. Quality is best with "
                  "Qwen3.5 2B.", "translate", languages=True),
            Field("llm.model", "Language model",
                  "LFM2.5 1.2B is fast on any CPU. Qwen3.5 2B rewrites selected text "
                  "noticeably better and is the better pick with a GPU.", "choice",
                  (Option("lfm2.5-1.2b-instruct-q4km", "LFM2.5 1.2B (recommended)"),
                   Option("qwen3.5-2b-q4km", "Qwen3.5 2B (better, slower on CPU)"),
                   Option("qwen3.5-0.8b-q4km", "Qwen3.5 0.8B (smallest)"))),
            Field("llm.gpu", "Run the language model on",
                  "A graphics card makes polishing and speak-to-edit several times faster. "
                  "If it fails, Keynone falls back to the CPU.", "gpu"),
            Field("cleanup.llm_timeout_seconds", "AI time limit",
                  "If the model takes longer, the rule-based text is used instead.", "float",
                  minimum=0.5, maximum=30, step=0.5, unit="seconds", advanced=True),
            Field("llm.threads", "Language model threads", "CPU threads for the model.", "int",
                  minimum=1, maximum=32, advanced=True),
            Field("llm.context", "Context size",
                  "Tokens per request. Larger allows longer selections for speak-to-edit and "
                  "uses more memory.", "int", minimum=512, maximum=32_768, step=512,
                  unit="tokens", advanced=True),
            Field("llm.gpu_layers", "Layers on the GPU", "99 = the whole model.", "int",
                  minimum=0, maximum=999, advanced=True),
        ),
    ),
    Section(
        "typing", "Typing into apps",
        "How text reaches the app you are working in.",
        (
            Field("insert.method", "Insert text by",
                  "Pasting is fast and keeps every character; your clipboard is put back "
                  "afterwards. Typing works in the few apps that block paste.", "choice",
                  (Option("paste", "Pasting (recommended)"), Option("type", "Typing"))),
            Field("insert.smart_spacing", "Smart spacing",
                  "Reads the few characters around the cursor to get spaces and capital "
                  "letters right mid-sentence.", "bool"),
            Field("insert.trailing_space", "Add a space after each dictation", "", "bool"),
            Field("insert.restore_clipboard", "Restore my clipboard after pasting", "", "bool"),
            Field("insert.paste_keys", "Paste shortcut", "", "hotkey",
                  (Option("ctrl+v", "ctrl+v"), Option("shift+insert", "shift+insert")),
                  advanced=True),
            Field("insert.copy_keys", "Copy shortcut",
                  "Used by speak-to-edit when an app can't tell what is selected.", "hotkey",
                  (Option("ctrl+c", "ctrl+c"), Option("ctrl+insert", "ctrl+insert")),
                  advanced=True),
            Field("insert.restore_delay_ms", "Wait before restoring the clipboard",
                  "Some slow apps read the clipboard late.", "int", minimum=50, maximum=5000,
                  step=50, unit="ms", advanced=True),
            Field("edit.timeout_seconds", "Speak-to-edit time limit", "", "float",
                  minimum=1, maximum=300, step=5, unit="seconds", advanced=True),
            Field("edit.max_selection_chars", "Longest selection to rewrite", "", "int",
                  minimum=100, maximum=100_000, step=500, unit="characters", advanced=True),
        ),
    ),
    Section(
        "dictionary", "Dictionary",
        "Names and jargon spelled your way. The language model gets them as hints too.",
        (
            Field("dictionary.words", "Words and names",
                  "One per line, written exactly as they should appear.", "words"),
            Field("dictionary.replacements", "Replacements",
                  "When the recognizer hears the left side, write the right side.",
                  "replacements"),
        ),
    ),
    Section(
        "apps", "App profiles",
        "Different behaviour for particular apps. The first profile that matches the active "
        "window wins; yours come before the built-in ones.",
        (Field("apps", "Your profiles", _STYLE_HELP, "apps"),),
    ),
    Section(
        "privacy", "Display & privacy",
        "The live overlay, the history and what goes into log files.",
        (
            Field("overlay.enabled", "Live overlay",
                  "Shows the text while you speak. It never takes the keyboard focus.", "bool"),
            Field("overlay.position", "Overlay position",
                  "On the screen that has the active window.", "choice",
                  (Option("bottom", "Bottom"), Option("top", "Top"))),
            Field("overlay.opacity", "Overlay opacity", "", "percent",
                  minimum=0.2, maximum=1.0, step=0.02),
            Field("history.enabled", "Keep a history of dictations",
                  "Stored only in data/history.db, never sent anywhere.", "bool"),
            Field("history.retention_days", "Keep the history for",
                  "Older entries are deleted. 0 keeps everything.", "int",
                  minimum=0, maximum=3650, step=7, unit="days"),
            Field("privacy.log_transcripts", "Write what I say into log files",
                  "For debugging only. Off by default.", "bool"),
        ),
    ),
)


def all_fields() -> list[Field]:
    return [f for section in SECTIONS for f in section.fields]
