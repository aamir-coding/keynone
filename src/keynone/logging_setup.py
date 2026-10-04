"""Logging to rotating files under data/logs (never transcripts unless enabled)."""

from __future__ import annotations

import logging
import logging.handlers
import sys

from keynone.paths import Paths, get_paths

_FORMAT = "%(asctime)s.%(msecs)03d %(levelname)-7s %(threadName)-10s %(name)s: %(message)s"


def setup_logging(name: str, *, verbose: bool = False, paths: Paths | None = None) -> None:
    paths = paths or get_paths()
    paths.logs.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    file_handler = logging.handlers.RotatingFileHandler(
        paths.logs / f"{name}.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(logging.Formatter(_FORMAT, datefmt="%Y-%m-%d %H:%M:%S"))
    root.addHandler(file_handler)
    if sys.stderr is not None and sys.stderr.isatty():
        console = logging.StreamHandler(sys.stderr)
        console.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        console.setLevel(logging.DEBUG if verbose else logging.WARNING)
        root.addHandler(console)
