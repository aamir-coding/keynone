"""The settings window's model: edits go into config.toml without losing anything."""

from __future__ import annotations

import shutil
import time
from pathlib import Path

import pytest

from keynone.config import Config, ConfigError, parse_config
from keynone.paths import Paths
from keynone.settings.model import (
    SettingsModel,
    all_keys,
    coerce,
    defaults,
    flatten,
    hotkey_problem,
    render,
)

DEFAULT_FILE = Paths(Path(".")).default_config


@pytest.fixture
def config_file(tmp_path: Path) -> Path:
    path = tmp_path / "config.toml"
    shutil.copyfile(DEFAULT_FILE, path)
    return path


def _model(path: Path) -> SettingsModel:
    return SettingsModel(path, DEFAULT_FILE)


def _later(path: Path, text: str) -> None:
    """Write as another program would, with a newer timestamp."""
    time.sleep(0.02)
    path.write_text(text, encoding="utf-8")


def test_defaults_match_the_shipped_file(config_file: Path) -> None:
    model = _model(config_file)
    assert model.error is None
    assert {k: model.get(k) for k in all_keys()} == defaults()
    assert not model.dirty


def test_a_change_is_written_in_place_keeping_every_comment(config_file: Path) -> None:
    before = config_file.read_text(encoding="utf-8")
    model = _model(config_file)
    model.set("hotkeys.dictate", "f9")
    model.set("audio.normalize_level", False)
    assert model.changed_keys() == ["hotkeys.dictate", "audio.normalize_level"]
    result = model.save()
    assert result.changed == ["hotkeys.dictate", "audio.normalize_level"]
    after = config_file.read_text(encoding="utf-8")
    changed = [(a, b) for a, b in zip(before.splitlines(), after.splitlines(), strict=True)
               if a != b]
    assert changed == [('dictate = "ctrl+win"', 'dictate = "f9"'),
                       ("normalize_level = true", "normalize_level = false")]
    assert not model.dirty
    assert model.get("hotkeys.dictate") == "f9"


def test_unknown_keys_and_inline_comments_survive(config_file: Path) -> None:
    text = config_file.read_text(encoding="utf-8").replace(
        'cancel = "esc"', 'cancel = "esc"   # my note\nfuture_option = 3')
    config_file.write_text(text, encoding="utf-8")
    model = _model(config_file)
    model.set("hotkeys.cancel", "f12")
    model.save()
    saved = config_file.read_text(encoding="utf-8")
    assert 'cancel = "f12"   # my note' in saved
    assert "future_option = 3" in saved


def test_keys_missing_from_the_file_are_added_to_their_table(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('[hotkeys]\ndictate = "f9"\n', encoding="utf-8")
    model = _model(path)
    assert model.get("overlay.enabled") is True  # the default
    model.set("overlay.enabled", False)
    model.set("hotkeys.cancel", "f12")
    model.save()
    config = parse_config(path.read_text(encoding="utf-8"))
    assert config.overlay.enabled is False
    assert config.hotkeys.cancel == "f12"
    assert config.hotkeys.dictate == "f9"


def test_dictionary_and_replacements(config_file: Path) -> None:
    model = _model(config_file)
    model.set("dictionary.words", ["Keynone", "PostgreSQL"])
    model.set("dictionary.replacements", {"chat gpt": "ChatGPT", "post gres": "Postgres"})
    model.save()
    text = config_file.read_text(encoding="utf-8")
    config = parse_config(text)
    assert config.dictionary.words == ["Keynone", "PostgreSQL"]
    assert config.dictionary.replacements == {"chat gpt": "ChatGPT", "post gres": "Postgres"}
    # new replacements go under their own header, not below the app-profile notes
    header = text.index("[dictionary.replacements]")
    assert header < text.index('"chat gpt" = "ChatGPT"') < text.index("# Per-app behaviour")

    model.set("dictionary.replacements", {"post gres": "Postgres"})
    model.save()
    assert parse_config(config_file.read_text(encoding="utf-8")).dictionary.replacements == {
        "post gres": "Postgres"}


def test_app_profiles_round_trip(config_file: Path) -> None:
    model = _model(config_file)
    profiles = [{"name": "notes", "match": ["obsidian.exe"], "style": "chat",
                 "drop_final_period": True},
                {"name": "jira", "title_match": ["Jira"], "style": "default", "llm_mode": "off"}]
    model.set("apps", profiles)
    model.save()
    config = parse_config(config_file.read_text(encoding="utf-8"))
    assert [p.name for p in config.apps] == ["notes", "jira"]
    assert config.apps[0].drop_final_period is True
    assert config.apps[1].llm_mode == "off"
    assert model.get("apps") == profiles

    model.set("apps", [])
    model.save()
    text = config_file.read_text(encoding="utf-8")
    assert parse_config(text).apps == []
    assert "\n[[apps]]" not in text  # only the commented example is left


def test_invalid_values_are_never_written(config_file: Path) -> None:
    before = config_file.read_text(encoding="utf-8")
    model = _model(config_file)
    model.set("overlay.opacity", 5.0)  # the config allows 0.2 .. 1.0
    with pytest.raises(ConfigError, match=r"overlay\.opacity"):
        model.save()
    assert config_file.read_text(encoding="utf-8") == before


def test_edits_made_elsewhere_meanwhile_are_kept(config_file: Path) -> None:
    model = _model(config_file)
    model.set("hotkeys.dictate", "f9")
    _later(config_file, config_file.read_text(encoding="utf-8").replace(
        "keep_warm_seconds = 300", "keep_warm_seconds = 60"))
    assert model.changed_on_disk()
    result = model.save()
    assert result.merged
    config = parse_config(config_file.read_text(encoding="utf-8"))
    assert config.hotkeys.dictate == "f9"
    assert config.lifecycle.keep_warm_seconds == 60
    assert model.get("lifecycle.keep_warm_seconds") == 60  # reloaded after saving


def test_a_broken_file_is_reported_and_not_overwritten(config_file: Path) -> None:
    config_file.write_text("[hotkeys\ndictate = 'f9'\n", encoding="utf-8")
    model = _model(config_file)
    assert model.error is not None
    model.set("hotkeys.cancel", "f12")
    with pytest.raises(ConfigError):
        model.save()
    assert config_file.read_text(encoding="utf-8") == "[hotkeys\ndictate = 'f9'\n"


def test_restore_defaults_keeps_a_backup(config_file: Path) -> None:
    config_file.write_text('[hotkeys]\ndictate = "f9"\n', encoding="utf-8")
    model = _model(config_file)
    backup = model.restore_defaults()
    assert backup.read_text(encoding="utf-8") == '[hotkeys]\ndictate = "f9"\n'
    assert model.get("hotkeys.dictate") == "ctrl+win"
    assert config_file.read_text(encoding="utf-8") == DEFAULT_FILE.read_text(encoding="utf-8")


def test_windows_line_endings_and_bom_are_handled(config_file: Path) -> None:
    text = config_file.read_text(encoding="utf-8")
    config_file.write_bytes(b"\xef\xbb\xbf" + text.replace("\n", "\r\n").encode())
    model = _model(config_file)
    assert model.error is None
    model.set("hotkeys.dictate", "f9")
    model.save()
    raw = config_file.read_bytes()
    assert b'dictate = "f9"\r\n' in raw
    assert raw.count(b"\n") == raw.count(b"\r\n")  # the file keeps its line endings
    assert not raw.startswith(b"\xef\xbb\xbf")


def test_render_validates_the_result() -> None:
    text = DEFAULT_FILE.read_text(encoding="utf-8")
    assert 'dictate = "f9"' in render(text, {"hotkeys.dictate": "f9"})
    with pytest.raises(ConfigError, match=r"asr\.language"):
        render(text, {"asr.language": "not a language"})


def test_values_are_coerced_to_the_config_types() -> None:
    assert coerce("lifecycle.keep_warm_seconds", 60.0) == 60
    assert coerce("cleanup.llm_timeout_seconds", 3) == 3.0
    assert isinstance(coerce("cleanup.llm_timeout_seconds", 3), float)
    assert coerce("overlay.enabled", 1) is True
    assert coerce("apps", [{"name": "x", "match": ["a.exe"], "method": None, "title_match": []}]
                  ) == [{"name": "x", "match": ["a.exe"]}]


def test_flatten_covers_every_config_option() -> None:
    values = flatten(Config())
    assert "hotkeys.dictate" in values and "apps" in values
    assert values["dictionary.words"] == []


@pytest.mark.parametrize(("key", "text", "ok"), [
    ("hotkeys.dictate", "ctrl+win", True),
    ("hotkeys.dictate", "rctrl", True),
    ("hotkeys.dictate", "f9", True),
    ("hotkeys.dictate", "", False),
    ("hotkeys.dictate", "ctrl+banana", False),
    ("hotkeys.dictate", "a", False),  # you type with it
    ("hotkeys.dictate", "ctrl", False),  # starts countless shortcuts
    ("hotkeys.dictate", "ctrl+win+alt", False),  # the edit shortcut
    ("hotkeys.edit", "", True),  # turns speak-to-edit off
    ("hotkeys.edit", "ctrl+win", False),  # the dictation shortcut
    ("hotkeys.cancel", "", True),
    ("hotkeys.cancel", "esc", True),
    ("hotkeys.cancel", "win", False),  # part of ctrl+win
    ("insert.paste_keys", "shift+insert", True),
])
def test_hotkey_rules(key: str, text: str, ok: bool) -> None:
    values = {"hotkeys.dictate": "ctrl+win", "hotkeys.edit": "ctrl+win+alt",
              "hotkeys.cancel": "esc", "insert.paste_keys": "ctrl+v", "insert.copy_keys": "ctrl+c"}
    values[key] = text
    assert (hotkey_problem(key, text, values) is None) is ok
