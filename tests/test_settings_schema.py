"""The settings window shows every option of config.toml, with sensible limits."""

from __future__ import annotations

import typing
from collections import Counter
from pathlib import Path

import pytest

from keynone.config import ConfigError, parse_config
from keynone.paths import Paths
from keynone.settings.model import all_keys, field_type, render
from keynone.settings.schema import SECTIONS, Field, all_fields

DEFAULT_TEXT = Paths(Path(".")).default_config.read_text(encoding="utf-8")


def test_every_config_option_is_in_the_window_exactly_once() -> None:
    keys = Counter(f.key for f in all_fields())
    assert [k for k, n in keys.items() if n > 1] == []
    assert set(keys) == set(all_keys())


def test_sections_have_unique_ids_and_content() -> None:
    ids = [s.id for s in SECTIONS]
    assert len(ids) == len(set(ids)) and "about" not in ids
    assert all(s.fields and s.title and s.description for s in SECTIONS)


def test_kinds_match_the_config_types() -> None:
    for f in all_fields():
        if f.key == "apps":
            assert f.kind == "apps"
            continue
        tp = field_type(f.key)
        if f.kind == "bool":
            assert tp is bool, f.key
        elif f.kind == "int":
            assert tp is int, f.key
        elif f.kind in ("float", "percent"):
            assert tp is float, f.key
        elif f.kind == "words":
            assert typing.get_origin(tp) is list, f.key
        elif f.kind in ("replacements", "snippets"):
            assert typing.get_origin(tp) is dict, f.key
        else:
            assert tp is str or typing.get_origin(tp) is typing.Literal, f.key


def test_choices_are_values_the_config_accepts() -> None:
    for f in all_fields():
        if f.kind != "choice":
            continue
        for option in f.options:
            text = render(DEFAULT_TEXT, {f.key: option.value})
            parse_config(text)  # raises if not accepted


@pytest.mark.parametrize("f", [f for f in all_fields() if f.minimum is not None],
                         ids=lambda f: f.key)
def test_number_limits_are_accepted_by_the_config(f: Field) -> None:
    assert f.minimum is not None and f.maximum is not None
    for value in (f.minimum, f.maximum):
        typed = int(value) if f.kind == "int" else float(value)
        try:
            parse_config(render(DEFAULT_TEXT, {f.key: typed}))
        except ConfigError as exc:  # pragma: no cover - the message says which
            pytest.fail(f"{f.key} = {typed} is offered but rejected: {exc}")


def test_defaults_lie_within_the_offered_limits() -> None:
    config = parse_config(DEFAULT_TEXT)
    for f in all_fields():
        if f.minimum is None or f.maximum is None:
            continue
        section, name = f.key.split(".")
        value = getattr(getattr(config, section), name)
        assert f.minimum <= value <= f.maximum, f.key
