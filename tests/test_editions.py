"""The two editions: same code, the language pack decides what can run."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from keynone.asr import MULTILINGUAL_MODEL as ASR_MULTILINGUAL
from keynone.assets import REGISTRY, get_asset
from keynone.config import Config
from keynone.editions import (
    BASE,
    EDITION_FILE,
    EDITIONS,
    ENGLISH_MODEL,
    LANGUAGE_PACK,
    LANGUAGES,
    download_edition,
    installed_edition,
    language_pack_installed,
    without_missing_language_pack,
)
from keynone.paths import Paths


@pytest.fixture
def paths(tmp_path: Path) -> Paths:
    paths = Paths(tmp_path)
    paths.ensure()
    return paths


def _install_pack(paths: Paths) -> None:
    for asset_id in LANGUAGE_PACK:
        directory = get_asset(asset_id).directory(paths)
        directory.mkdir(parents=True)
        (directory / ".complete.json").write_text("{}", encoding="utf-8")  # as setup leaves it


def test_editions_are_built_from_registered_assets() -> None:
    known = {asset.id for asset in REGISTRY}
    for edition in EDITIONS.values():
        assert set(edition.assets) <= known, edition.id
    assert set(LANGUAGES.assets) == set(BASE.assets) | set(LANGUAGE_PACK)
    assert ENGLISH_MODEL in BASE.assets and ENGLISH_MODEL == Config().asr.model
    assert Config().llm.model in BASE.assets
    assert ASR_MULTILINGUAL in LANGUAGE_PACK
    assert LANGUAGES.download_mb > BASE.download_mb


def test_the_installed_pack_decides_the_edition(paths: Paths) -> None:
    assert not language_pack_installed(paths)
    assert installed_edition(paths) is BASE
    _install_pack(paths)
    assert language_pack_installed(paths)
    assert installed_edition(paths) is LANGUAGES


def test_a_download_names_its_edition(paths: Paths) -> None:
    assert download_edition(paths) is BASE  # a git checkout has no edition.txt
    (paths.home / EDITION_FILE).write_text("languages\n", encoding="utf-8")
    assert download_edition(paths) is LANGUAGES
    (paths.home / EDITION_FILE).write_text("something else", encoding="utf-8")
    assert download_edition(paths) is BASE


def test_english_settings_are_never_touched(paths: Paths) -> None:
    config = Config()
    assert without_missing_language_pack(config, paths) == (config, [])
    assert without_missing_language_pack(config, paths)[0] is config


@pytest.mark.parametrize("language", ["auto", "hi", "es-ES"])
def test_other_languages_fall_back_to_english_without_the_pack(paths: Paths,
                                                               language: str) -> None:
    config = Config()
    config.asr.language = language
    limited, notes = without_missing_language_pack(config, paths)
    assert limited.asr.language == "en"
    assert limited.asr.model == ENGLISH_MODEL
    assert len(notes) == 1 and "keynone setup --edition languages" in notes[0]
    assert config.asr.language == language  # the loaded config itself is not changed
    assert dataclasses.replace(limited, asr=config.asr) == config  # nothing else changed


def test_translation_and_the_multilingual_model_need_the_pack(paths: Paths) -> None:
    config = Config()
    config.cleanup.translate_to = "English"
    config.asr.model = LANGUAGE_PACK[0]
    limited, notes = without_missing_language_pack(config, paths)
    assert limited.cleanup.translate_to == ""
    assert limited.asr.model == ENGLISH_MODEL
    assert len(notes) == 2


def test_with_the_pack_everything_is_allowed(paths: Paths) -> None:
    _install_pack(paths)
    config = Config()
    config.asr.language = "auto"
    config.cleanup.translate_to = "Spanish"
    assert without_missing_language_pack(config, paths) == (config, [])
