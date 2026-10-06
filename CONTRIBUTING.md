# Contributing to Keynone

Thanks for helping. Keynone is a small project with a few firm rules: it works
offline, collects nothing, and keeps every file it writes inside its own folder.
Changes that keep those rules - and keep dictation quality where it is - are very
welcome.

## Set up

You need Windows 10/11 and [uv](https://docs.astral.sh/uv/).

```bash
git clone <your fork> keynone && cd keynone
. .\scripts\env.ps1                 # every uv/pip/Hugging Face cache stays in this folder
uv python install 3.12.13 --no-bin --no-registry
uv sync                             # includes the dev tools (pytest, ruff, mypy)
uv run keynone setup                # the models (~1.4 GB); --edition languages for the pack
```

Dot-source `scripts\env.ps1` in every new terminal before running `uv`.

## Before you open a pull request

```bash
uv run ruff check src tests scripts
uv run mypy
uv run pytest
```

* **Every bug fix comes with a test** that failed before the fix. Dictation output
  bugs go into `tests/test_dictation_regressions.py`.
* **Cleanup changes** (rules, prompts, guard) must not make any dictation worse.
  Run `uv run python scripts/stress_test.py text` (and `audio`, which needs the
  models) before and after, and say in the PR what changed. The 352 cases in
  `src/keynone/evaldata/dictation_stress*.jsonl` are the yardstick; add the case
  you fixed.
* **Desktop tests** type into their own window, open the microphone for ~2 s and
  start the LLM. They are opt-in:
  `$env:KEYNONE_DESKTOP_TESTS="1"; uv run pytest tests/test_desktop_*.py`.
* User-visible changes go into `CHANGELOG.md` under the next version.

## Code style

* `ruff` and `mypy` are configured in `pyproject.toml` (line length 100).
* Comments explain *why*, not what. Match the code around you.
* Text the user reads - settings labels, tray messages, errors - is plain
  language: say what happens and what to do, without jargon.
* Every option in `config.py` needs an entry in `src/keynone/settings/schema.py`
  (a test checks this) and a commented line in `default_config.toml`.

## Models and downloads

Models and runtimes are listed in `src/keynone/assets.py`. Each file is pinned to
its upstream SHA-256 digest and carries its license. A new model needs both, plus
a measurement on the reference machine (see `docs/01-feasibility-and-model-selection.md`).
What each edition installs is defined in `src/keynone/editions.py`.

## Releases (maintainers)

1. Bump the version in `pyproject.toml` and `src/keynone/__init__.py`, run `uv lock`.
2. In `CHANGELOG.md`, replace "unreleased" with the date.
3. Tag `vX.Y.Z` and push the tag. The release workflow builds both downloads
   (`scripts/build_release.py`), checksums and release notes, and publishes them.

## Code of conduct

Be kind and assume good intent. See [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
