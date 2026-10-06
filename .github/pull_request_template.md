## What and why

<!-- What does this change, and what problem does it solve? Link the issue if there is one. -->

## Checks

- [ ] `uv run ruff check src tests scripts`, `uv run mypy` and `uv run pytest` pass
- [ ] A bug fix comes with a test that failed before (`tests/test_dictation_regressions.py` for dictation output)
- [ ] Changes to the cleanup (rules, prompts, guard): `scripts/stress_test.py text` and, with models, `audio` show no case that got worse
- [ ] User-visible changes are in `CHANGELOG.md` and, where needed, the README / docs
- [ ] Nothing is written outside the Keynone folder and nothing goes over the network after setup
