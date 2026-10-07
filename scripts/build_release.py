"""Build the two release downloads from this checkout.

    uv run python scripts/build_release.py [--out dist] [--working-tree]

    dist/keynone-<version>-windows.zip            edition.txt = base
    dist/keynone-<version>-windows-languages.zip  edition.txt = languages
    dist/SHA256SUMS.txt
    dist/release-notes.md                          downloads table + CHANGELOG section

Both zips hold the same program: the files git tracks, minus what is only for
developing Keynone (tests, CI configuration, the stress-test data and scripts;
contributors use the git repository). Only ``edition.txt`` differs, and it only
decides which models ``scripts/install.ps1`` downloads (see ``keynone.editions``).
Models are never inside a zip: setup downloads them from their publishers and
checks each file against a pinned SHA-256 digest.

Everything sits in a top-level ``keynone/`` folder, so a newer zip can be
extracted over an older one: settings, history and models stay (they live in
the git-ignored ``data/``, ``models/`` and ``bin/``).

The zips are reproducible: entries are sorted and dated with the last commit
(or ``SOURCE_DATE_EPOCH``). ``--working-tree`` also takes files that are not
committed yet, to try a release locally.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from keynone import __version__  # noqa: E402
from keynone.editions import EDITION_FILE, EDITIONS  # noqa: E402

EXCLUDED_PREFIXES = ("tests/", ".github/")
EXCLUDED_FILES = {
    ".gitignore", ".gitattributes", EDITION_FILE,
    # For working on Keynone, not for using it: they stay in the git repository.
    "scripts/build_release.py", "scripts/make_test_audio.py", "scripts/stress_test.py",
    "scripts/tts_windows.ps1",
    "src/keynone/evaldata/dictation_stress.jsonl",
    "src/keynone/evaldata/dictation_stress_holdout.jsonl",
    "src/keynone/evaldata/speak_to_edit_eval.jsonl",
}
TOP = "keynone"


def release_files(working_tree: bool) -> list[str]:
    """Relative POSIX paths of the files that go into a release, sorted."""
    command = ["git", "ls-files", "-z", "--cached"]
    if working_tree:
        command += ["--others", "--exclude-standard"]
    out = subprocess.run(command, cwd=ROOT, check=True, capture_output=True).stdout
    names = {name for name in out.decode("utf-8").split("\0") if name}
    return sorted(name for name in names
                  if not name.startswith(EXCLUDED_PREFIXES) and name not in EXCLUDED_FILES
                  and (ROOT / name).is_file())


def build_time() -> tuple[int, int, int, int, int, int]:
    epoch = os.environ.get("SOURCE_DATE_EPOCH")
    if not epoch:
        result = subprocess.run(["git", "log", "-1", "--format=%ct"], cwd=ROOT,
                                capture_output=True, text=True, check=False)
        epoch = result.stdout.strip() or "1767225600"  # 2026-01-01 before the first commit
    return time.gmtime(max(int(epoch), 315532800))[:6]  # zip dates start in 1980


def write_zip(target: Path, files: list[str], edition: str,
              stamp: tuple[int, int, int, int, int, int]) -> None:
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for name in files:
            info = zipfile.ZipInfo(f"{TOP}/{name}", date_time=stamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, (ROOT / name).read_bytes())
        info = zipfile.ZipInfo(f"{TOP}/{EDITION_FILE}", date_time=stamp)
        info.external_attr = 0o644 << 16
        zf.writestr(info, f"{edition}\n")


def changelog_section(version: str) -> str:
    """The CHANGELOG.md entry for ``version`` (without its heading)."""
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    lines, inside = [], False
    for line in text.splitlines():
        if line.startswith("## "):
            if inside:
                break
            inside = line.startswith(f"## [{version}]")
            continue
        if inside:
            lines.append(line)
    return "\n".join(lines).strip()


def zip_name(edition_id: str, version: str) -> str:
    suffix = "" if edition_id == "base" else f"-{edition_id}"
    return f"keynone-{version}-windows{suffix}.zip"


def release_notes(version: str) -> str:
    rows = [f"| {edition.name} | {edition.summary} | `{zip_name(edition.id, version)}` "
            f"| ~{edition.download_mb / 1000:.1f} GB |" for edition in EDITIONS.values()]
    return "\n".join([
        "## Downloads",
        "",
        "Both are the same program; they differ only in the models setup downloads.",
        "",
        "| Edition | What it does | File | Models (downloaded during setup) |",
        "|---|---|---|---|",
        *rows,
        "",
        "Extract the zip, then in PowerShell: `powershell -ExecutionPolicy Bypass -File "
        ".\\keynone\\scripts\\install.ps1` (needs [uv](https://docs.astral.sh/uv/)). "
        "The language pack can be added later with `keynone setup --edition languages`. "
        "Check the download against `SHA256SUMS.txt`.",
        "",
        changelog_section(version),
        "",
    ])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=ROOT / "dist")
    parser.add_argument("--working-tree", action="store_true",
                        help="include files that are not committed yet (local test builds)")
    parser.add_argument("--tag", help="fail unless this git tag (e.g. v0.2.0) names the version")
    args = parser.parse_args(argv)
    if args.tag is not None and args.tag != f"v{__version__}":
        parser.error(f"tag {args.tag} does not match the version {__version__} (src/keynone)")
    if not changelog_section(__version__):
        parser.error(f"CHANGELOG.md has no entry for {__version__}")
    files = release_files(args.working_tree)
    for required in ("Keynone.cmd", "pyproject.toml", "uv.lock", "scripts/install.ps1",
                     "LICENSE", "README.md", "src/keynone/__main__.py"):
        if required not in files:
            parser.error(f"{required} is missing from the release (not committed yet?)")
    args.out.mkdir(parents=True, exist_ok=True)
    stamp = build_time()
    sums = []
    for edition in EDITIONS.values():
        target = args.out / zip_name(edition.id, __version__)
        write_zip(target, files, edition.id, stamp)
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        sums.append(f"{digest}  {target.name}")
        print(f"{target.name}: {len(files) + 1} files, {target.stat().st_size / 1e6:.2f} MB, "
              f"{edition.name} (~{edition.download_mb / 1000:.1f} GB of models at setup)")
    (args.out / "SHA256SUMS.txt").write_text("\n".join(sums) + "\n", encoding="utf-8")
    (args.out / "release-notes.md").write_text(release_notes(__version__), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
