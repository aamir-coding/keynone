"""Filesystem layout.

Keynone is portable: everything it reads or writes lives under a single root
directory (``KEYNONE_HOME``). By default that is the project folder itself, so
the app never writes to %APPDATA%, the registry, or other system locations.

    <home>/models/   downloaded speech / language models   (git-ignored)
    <home>/bin/      downloaded native runtimes (llama.cpp) (git-ignored)
    <home>/data/     config.toml, history.db, logs/, cache/ (git-ignored)
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from functools import cache
from pathlib import Path


def _detect_home() -> Path:
    env = os.environ.get("KEYNONE_HOME")
    if env:
        return Path(env).expanduser().resolve()
    repo = Path(__file__).resolve().parents[2]  # <repo>/src/keynone/paths.py
    if (repo / "pyproject.toml").is_file():
        return repo
    # Non-editable install inside <home>/.venv: the venv's parent is home.
    return Path(sys.prefix).resolve().parent


@dataclass(frozen=True)
class Paths:
    home: Path

    @property
    def models(self) -> Path:
        return self.home / "models"

    @property
    def bin(self) -> Path:
        return self.home / "bin"

    @property
    def data(self) -> Path:
        return self.home / "data"

    @property
    def logs(self) -> Path:
        return self.data / "logs"

    @property
    def cache(self) -> Path:
        return self.data / "cache"

    @property
    def config_file(self) -> Path:
        return self.data / "config.toml"

    @property
    def history_db(self) -> Path:
        return self.data / "history.db"

    @property
    def default_config(self) -> Path:
        """Commented default config shipped inside the package."""
        return Path(__file__).with_name("default_config.toml")

    def ensure(self) -> None:
        for directory in (self.models, self.bin, self.data, self.logs, self.cache):
            directory.mkdir(parents=True, exist_ok=True)


@cache
def get_paths() -> Paths:
    return Paths(_detect_home())
