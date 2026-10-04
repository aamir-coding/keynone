"""Typed, validated configuration loaded from ``data/config.toml``.

The user-facing file is a commented copy of ``default_config.toml``. Unknown
keys are reported as warnings; wrong types or values raise :class:`ConfigError`
with the offending key path, so mistakes are easy to fix.
"""

from __future__ import annotations

import dataclasses
import logging
import shutil
import tomllib
import types
import typing
from dataclasses import dataclass, field
from typing import Any, Literal, get_args, get_origin

from keynone.paths import Paths, get_paths

log = logging.getLogger(__name__)

Style = Literal["default", "chat", "email", "code"]
LlmMode = Literal["auto", "always", "off"]
InsertMethod = Literal["paste", "type"]


class ConfigError(ValueError):
    pass


@dataclass
class HotkeyConfig:
    dictate: str = "ctrl+win"
    cancel: str = "esc"
    hold_threshold_ms: int = 300
    double_tap_ms: int = 400


@dataclass
class LifecycleConfig:
    keep_warm_seconds: int = 300


@dataclass
class AudioConfig:
    device: str = ""
    cues: bool = True
    max_session_seconds: int = 600
    handsfree_silence_stop_seconds: float = 0.0


@dataclass
class AsrConfig:
    model: str = "nemotron-en-560ms-int8"
    threads: int = 4
    endpoint_silence_seconds: float = 0.8


@dataclass
class CleanupConfig:
    llm_mode: LlmMode = "auto"
    remove_fillers: bool = True
    voice_commands: bool = True
    llm_timeout_seconds: float = 4.0


@dataclass
class LlmConfig:
    model: str = "lfm2.5-1.2b-instruct-q4km"
    threads: int = 4
    context: int = 2048


@dataclass
class InsertConfig:
    method: InsertMethod = "paste"
    paste_keys: str = "ctrl+v"
    restore_clipboard: bool = True
    restore_delay_ms: int = 400
    trailing_space: bool = True


@dataclass
class OverlayConfig:
    enabled: bool = True
    position: Literal["bottom", "top"] = "bottom"
    opacity: float = 0.94


@dataclass
class HistoryConfig:
    enabled: bool = True
    retention_days: int = 30


@dataclass
class PrivacyConfig:
    log_transcripts: bool = False


@dataclass
class DictionaryConfig:
    words: list[str] = field(default_factory=list)
    replacements: dict[str, str] = field(default_factory=dict)


@dataclass
class AppProfile:
    name: str = "custom"
    match: list[str] = field(default_factory=list)
    title_match: list[str] = field(default_factory=list)
    style: Style = "default"
    method: InsertMethod | None = None
    paste_keys: str | None = None
    trailing_space: bool | None = None
    llm_mode: LlmMode | None = None
    drop_final_period: bool | None = None

    def matches(self, exe: str, title: str) -> bool:
        exe_l, title_l = exe.lower(), title.lower()
        if any(exe_l == m.lower() for m in self.match):
            return True
        return any(t.lower() in title_l for t in self.title_match)


BUILTIN_PROFILES: tuple[AppProfile, ...] = (
    AppProfile(
        name="webmail",
        title_match=["gmail", "outlook.com", "outlook - ", "proton mail", "yahoo mail"],
        style="email",
    ),
    AppProfile(
        name="chat",
        match=[
            "slack.exe", "discord.exe", "whatsapp.exe", "whatsapp.root.exe", "telegram.exe",
            "ms-teams.exe", "teams.exe", "signal.exe", "messenger.exe",
        ],
        title_match=["whatsapp", "messenger", "slack", "discord"],
        style="chat",
        drop_final_period=True,
    ),
    AppProfile(
        name="email",
        match=["outlook.exe", "olk.exe", "thunderbird.exe", "hxoutlook.exe"],
        style="email",
    ),
    AppProfile(
        name="terminal",
        match=[
            "windowsterminal.exe", "wt.exe", "cmd.exe", "powershell.exe", "pwsh.exe",
            "conhost.exe", "openconsole.exe", "wezterm-gui.exe", "alacritty.exe",
        ],
        style="code",
        trailing_space=False,
        llm_mode="off",
    ),
    AppProfile(name="mintty", match=["mintty.exe"], style="code", paste_keys="shift+insert",
               trailing_space=False, llm_mode="off"),
    AppProfile(
        name="code",
        match=["code.exe", "cursor.exe", "windsurf.exe", "idea64.exe", "pycharm64.exe",
               "devenv.exe", "sublime_text.exe", "notepad++.exe", "zed.exe"],
        style="code",
        trailing_space=False,
    ),
)


@dataclass
class Config:
    hotkeys: HotkeyConfig = field(default_factory=HotkeyConfig)
    lifecycle: LifecycleConfig = field(default_factory=LifecycleConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    asr: AsrConfig = field(default_factory=AsrConfig)
    cleanup: CleanupConfig = field(default_factory=CleanupConfig)
    llm: LlmConfig = field(default_factory=LlmConfig)
    insert: InsertConfig = field(default_factory=InsertConfig)
    overlay: OverlayConfig = field(default_factory=OverlayConfig)
    history: HistoryConfig = field(default_factory=HistoryConfig)
    privacy: PrivacyConfig = field(default_factory=PrivacyConfig)
    dictionary: DictionaryConfig = field(default_factory=DictionaryConfig)
    apps: list[AppProfile] = field(default_factory=list)

    def profile_for(self, exe: str, title: str) -> AppProfile:
        for profile in (*self.apps, *BUILTIN_PROFILES):
            if profile.matches(exe, title):
                return profile
        return AppProfile(name="default")


# --------------------------------------------------------------------------- loading


def _type_name(tp: Any) -> str:
    return getattr(tp, "__name__", None) or str(tp).replace("typing.", "")


def _coerce(value: Any, tp: Any, where: str) -> Any:
    origin = get_origin(tp)
    if origin is Literal:
        choices = get_args(tp)
        if value not in choices:
            raise ConfigError(f"{where}: expected one of {list(choices)}, got {value!r}")
        return value
    if origin in (types.UnionType, typing.Union):
        if value is None and type(None) in get_args(tp):
            return None
        errors = []
        for option in get_args(tp):
            if option is type(None):
                continue
            try:
                return _coerce(value, option, where)
            except ConfigError as exc:
                errors.append(str(exc))
        raise ConfigError("; ".join(errors))
    if origin is list:
        (item_tp,) = get_args(tp)
        if not isinstance(value, list):
            raise ConfigError(f"{where}: expected a list, got {type(value).__name__}")
        return [_coerce(v, item_tp, f"{where}[{i}]") for i, v in enumerate(value)]
    if origin is dict:
        key_tp, val_tp = get_args(tp)
        if not isinstance(value, dict):
            raise ConfigError(f"{where}: expected a table, got {type(value).__name__}")
        return {
            _coerce(k, key_tp, where): _coerce(v, val_tp, f"{where}.{k}") for k, v in value.items()
        }
    if dataclasses.is_dataclass(tp):
        if not isinstance(value, dict):
            raise ConfigError(f"{where}: expected a table, got {type(value).__name__}")
        return _build(tp, value, where)
    if tp is float and isinstance(value, int) and not isinstance(value, bool):
        return float(value)
    if tp is bool and not isinstance(value, bool):
        raise ConfigError(f"{where}: expected true/false, got {value!r}")
    if tp is int and (isinstance(value, bool) or not isinstance(value, int)):
        raise ConfigError(f"{where}: expected an integer, got {value!r}")
    if not isinstance(value, tp):
        raise ConfigError(f"{where}: expected {_type_name(tp)}, got {type(value).__name__}")
    return value


def _build(cls: Any, data: dict[str, Any], where: str) -> Any:
    hints = typing.get_type_hints(cls)
    known = {f.name for f in dataclasses.fields(cls)}
    for key in data:
        if key not in known:
            log.warning("config: ignoring unknown key %s.%s", where, key)
    kwargs = {
        name: _coerce(data[name], hints[name], f"{where}.{name}" if where else name)
        for name in known
        if name in data
    }
    return cls(**kwargs)


def _validate(cfg: Config) -> None:
    def check(ok: bool, message: str) -> None:
        if not ok:
            raise ConfigError(message)

    check(cfg.asr.threads >= 1, "asr.threads must be >= 1")
    check(cfg.llm.threads >= 1, "llm.threads must be >= 1")
    check(cfg.llm.context >= 512, "llm.context must be >= 512")
    check(0.2 <= cfg.overlay.opacity <= 1.0, "overlay.opacity must be within 0.2..1.0")
    check(cfg.lifecycle.keep_warm_seconds >= 0, "lifecycle.keep_warm_seconds must be >= 0")
    check(cfg.cleanup.llm_timeout_seconds > 0, "cleanup.llm_timeout_seconds must be > 0")
    check(0.2 <= cfg.asr.endpoint_silence_seconds <= 5, "asr.endpoint_silence_seconds: 0.2..5")
    check(cfg.audio.max_session_seconds >= 5, "audio.max_session_seconds must be >= 5")


def parse_config(text: str) -> Config:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML: {exc}") from exc
    cfg: Config = _build(Config, data, "")
    _validate(cfg)
    return cfg


def load_config(paths: Paths | None = None) -> Config:
    """Load ``data/config.toml``, creating it from the defaults on first run."""
    paths = paths or get_paths()
    paths.ensure()
    if not paths.config_file.exists():
        shutil.copyfile(paths.default_config, paths.config_file)
    # utf-8-sig: editors such as older Notepad save UTF-8 with a BOM, which TOML rejects.
    return parse_config(paths.config_file.read_text(encoding="utf-8-sig"))


def config_mtime(paths: Paths | None = None) -> float:
    paths = paths or get_paths()
    try:
        return paths.config_file.stat().st_mtime
    except FileNotFoundError:
        return 0.0
