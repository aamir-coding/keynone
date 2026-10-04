from pathlib import Path

import pytest

from keynone.config import ConfigError, load_config, parse_config
from keynone.paths import Paths


def test_shipped_default_config_parses() -> None:
    default = Path(__file__).resolve().parents[1] / "src" / "keynone" / "default_config.toml"
    assert not default.read_bytes().startswith(b"\xef\xbb\xbf"), "default config must not have a BOM"
    config = parse_config(default.read_text(encoding="utf-8"))
    assert config.asr.model == "nemotron-en-560ms-int8"
    assert config.cleanup.llm_mode == "auto"


def test_load_config_creates_copy_and_accepts_bom(tmp_path: Path) -> None:
    paths = Paths(tmp_path)
    config = load_config(paths)
    assert paths.config_file.exists()
    assert config.hotkeys.dictate == "ctrl+win"
    text = paths.config_file.read_text(encoding="utf-8")
    paths.config_file.write_bytes(b"\xef\xbb\xbf" + text.replace('"ctrl+win"', '"rctrl"').encode())
    assert load_config(paths).hotkeys.dictate == "rctrl"


@pytest.mark.parametrize(
    ("toml", "message"),
    [
        ("[asr]\nthreads = 0", "asr.threads"),
        ("[cleanup]\nllm_mode = 'sometimes'", "expected one of"),
        ("[overlay]\nenabled = 'yes'", "true/false"),
        ("[hotkeys\n", "invalid TOML"),
    ],
)
def test_invalid_config_reports_the_key(toml: str, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        parse_config(toml)


def test_app_profiles_user_first_then_builtin() -> None:
    config = parse_config(
        '[[apps]]\nname = "mine"\nmatch = ["slack.exe"]\nstyle = "email"\n'
    )
    assert config.profile_for("Slack.exe", "").name == "mine"
    assert config.profile_for("discord.exe", "").style == "chat"
    assert config.profile_for("chrome.exe", "Inbox - Gmail").style == "email"
    assert config.profile_for("notepad.exe", "Untitled").name == "default"
