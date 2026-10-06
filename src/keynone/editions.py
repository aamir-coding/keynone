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

from dataclasses import dataclass

from keynone.paths import Paths, get_paths

# Defined here rather than in keynone.asr so the settings window and the
# command line can ask about editions without loading numpy and sherpa-onnx.
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
    ("nemotron-en-560ms-int8", "lfm2.5-1.2b-instruct-q4km", "llama.cpp-b11398-cpu-x64"),
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
