"""The two editions of Keynone. Both are built from this one codebase.

* **Keynone** (``base``): English dictation with live preview, cleanup,
  speak-to-edit and the optional GPU boost. About 1.4 GB of models.
* **Keynone + languages** (``languages``): everything above, plus the language
  pack. The pack is the multilingual speech model (40 locales, 651 MB) and the
  features built on it: dictating in other languages (FR-39), language-aware
  cleanup (FR-40, FR-41) and translating every dictation (FR-42).

What Keynone can do depends only on what is installed. Adding the pack later
(``keynone setup --edition languages``) turns a base install into the
languages edition, and removing it (``keynone setup --remove ...``) turns it
back. A release download names its edition in ``edition.txt`` in the program
folder; that only decides what a plain ``keynone setup`` downloads.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import TYPE_CHECKING

from keynone.paths import Paths, get_paths

if TYPE_CHECKING:
    from keynone.config import Config

# Defined here rather than in keynone.asr so the settings window and the
# command line can ask about editions without loading numpy and sherpa-onnx.
ENGLISH_MODEL = "nemotron-en-560ms-int8"
MULTILINGUAL_MODEL = "nemotron3.5-multi-560ms-int8"
LANGUAGE_PACK: tuple[str, ...] = (MULTILINGUAL_MODEL,)
EDITION_FILE = "edition.txt"


@dataclass(frozen=True)
class Edition:
    id: str
    name: str
    summary: str
    assets: tuple[str, ...]  # what `keynone setup --edition <id>` downloads
    download_mb: int  # models + runtime on disk after setup


BASE = Edition(
    "base", "Keynone",
    "English dictation, cleanup, speak-to-edit",
    (ENGLISH_MODEL, "lfm2.5-1.2b-instruct-q4km", "llama.cpp-b11398-cpu-x64"),
    1375,
)
LANGUAGES = Edition(
    "languages", "Keynone + languages",
    "Adds dictation in 40 languages, language-aware cleanup and translation",
    (*BASE.assets, *LANGUAGE_PACK),
    2026,
)
EDITIONS: dict[str, Edition] = {e.id: e for e in (BASE, LANGUAGES)}


def language_pack_installed(paths: Paths | None = None) -> bool:
    from keynone.assets import get_asset

    paths = paths or get_paths()
    return all(get_asset(asset_id).is_installed(paths) for asset_id in LANGUAGE_PACK)


def installed_edition(paths: Paths | None = None) -> Edition:
    """The edition this installation currently is (decided by the language pack)."""
    return LANGUAGES if language_pack_installed(paths) else BASE


def download_edition(paths: Paths | None = None) -> Edition:
    """The edition the release download was (``edition.txt``); base when absent."""
    paths = paths or get_paths()
    try:
        name = (paths.home / EDITION_FILE).read_text(encoding="utf-8").strip().lower()
    except OSError:
        return BASE
    return EDITIONS.get(name, BASE)


ADD_LANGUAGE_PACK = "keynone setup --edition languages"


def without_missing_language_pack(config: Config, paths: Paths | None = None
                                  ) -> tuple[Config, list[str]]:
    """``config`` as this installation can run it, plus what was changed and why.

    Without the language pack, dictation stays in English (with the English model)
    and nothing is translated, instead of failing because a model is missing.
    With the pack, ``config`` is returned unchanged.
    """
    if language_pack_installed(paths):
        return config, []
    notes: list[str] = []
    asr, cleanup = config.asr, config.cleanup
    if asr.language.split("-")[0].lower() != "en":
        notes.append(f'asr.language = "{asr.language}" needs the language pack - dictating in '
                     f"English. Add it with: {ADD_LANGUAGE_PACK}")
        asr = dataclasses.replace(asr, language="en")
    if asr.model in LANGUAGE_PACK:
        notes.append(f"asr.model = {asr.model} needs the language pack - using the English "
                     f"model. Add it with: {ADD_LANGUAGE_PACK}")
        asr = dataclasses.replace(asr, model=ENGLISH_MODEL)
    if cleanup.translate_to.strip():
        notes.append(f'cleanup.translate_to = "{cleanup.translate_to}" needs the language pack '
                     f"- not translating. Add it with: {ADD_LANGUAGE_PACK}")
        cleanup = dataclasses.replace(cleanup, translate_to="")
    if not notes:
        return config, []
    return dataclasses.replace(config, asr=asr, cleanup=cleanup), notes
