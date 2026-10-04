"""Registry of downloadable models/runtimes and a verified downloader.

Every file is pinned to an upstream-published SHA-256 digest, downloaded over
HTTPS to a ``.part`` file (resumable), verified, then atomically renamed.
Archives are extracted with path-traversal protection.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import tarfile
import time
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

from keynone.paths import Paths, get_paths

log = logging.getLogger(__name__)

AssetKind = Literal["asr", "llm", "runtime", "tts"]
ProgressFn = Callable[[str, int, int | None], None]

_HF = "https://huggingface.co/{repo}/resolve/main/{path}"
_CHUNK = 1 << 20
_USER_AGENT = "keynone-setup/0.1"
_MARKER = ".complete.json"


@dataclass(frozen=True)
class RemoteFile:
    url: str
    dest: str  # POSIX-style path relative to the asset directory
    sha256: str
    size: int | None = None


@dataclass(frozen=True)
class Asset:
    id: str
    kind: AssetKind
    title: str
    license: str
    source: str
    files: tuple[RemoteFile, ...]
    extract: bool = False  # unpack archives into the asset directory
    default: bool = False  # part of a plain `keynone setup`

    def directory(self, paths: Paths) -> Path:
        root = paths.bin if self.kind == "runtime" else paths.models
        return root / self.id

    def is_installed(self, paths: Paths) -> bool:
        return (self.directory(paths) / _MARKER).is_file()


def _hf(repo: str, path: str, sha256: str, size: int | None = None) -> RemoteFile:
    return RemoteFile(_HF.format(repo=repo, path=path), path, sha256, size)


_NEMOTRON_160 = "csukuangfj2/sherpa-onnx-nemotron-speech-streaming-en-0.6b-160ms-int8-2026-04-25"
_NEMOTRON_560 = "csukuangfj2/sherpa-onnx-nemotron-speech-streaming-en-0.6b-560ms-int8-2026-04-25"
_GH_ASR = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/{name}"


def _gh_asr(name: str, sha256: str) -> RemoteFile:
    return RemoteFile(_GH_ASR.format(name=name), name, sha256)

REGISTRY: tuple[Asset, ...] = (
    Asset(
        id="nemotron-en-160ms-int8",
        kind="asr",
        title="NVIDIA Nemotron Speech Streaming EN 0.6B (160 ms chunks, int8, sherpa-onnx)",
        license="NVIDIA Open Model License",
        source="https://huggingface.co/nvidia/nemotron-speech-streaming-en-0.6b",
        default=True,
        files=(
            _hf(
                _NEMOTRON_160,
                "encoder.int8.onnx",
                "71111f61b18e1e65e01e369434a5c0434868d2f44892742ae54240600c681209",
            ),
            _hf(
                _NEMOTRON_160,
                "decoder.int8.onnx",
                "0be9702c2f427a2b6bb241d298e0d3836a558de1f5b9fd3018f1cce6e2b3fa98",
            ),
            _hf(
                _NEMOTRON_160,
                "joiner.int8.onnx",
                "a35eac38a22ebceb04d230ed7afe0d68f446ba6914a036b97f14fece95967e23",
            ),
            _hf(
                _NEMOTRON_160,
                "tokens.txt",
                "dc0b4584ab2e4ddbf888425c076c61b736e7356a015250db7d307e6f1a8188ff",
                8952,
            ),
            _hf(
                _NEMOTRON_160,
                "test_wavs/0.wav",
                "6bc58a4efdf20daac252b6b1502632601a71efe0308f6757dc1eda34891a7e4f",
            ),
            _hf(
                _NEMOTRON_160,
                "test_wavs/1.wav",
                "5143a6ba93c4b274e2c4ac22deb75c2c48936c853f0519add1de828b6c79cc5a",
            ),
            _hf(
                _NEMOTRON_160,
                "test_wavs/trans.txt",
                "b9ac44e7b794abb1a2d5faf0005e98a665971e7ac2ed15435832cc34edaa9100",
                449,
            ),
        ),
    ),
    Asset(
        id="nemotron-en-560ms-int8",
        kind="asr",
        title="NVIDIA Nemotron Speech Streaming EN 0.6B (560 ms chunks, int8, sherpa-onnx)",
        license="NVIDIA Open Model License",
        source="https://huggingface.co/nvidia/nemotron-speech-streaming-en-0.6b",
        files=(
            _hf(
                _NEMOTRON_560,
                "encoder.int8.onnx",
                "7d932213491ad355c6e5576705dc3494731a52af87d7a1b954559340147909d8",
            ),
            _hf(
                _NEMOTRON_560,
                "decoder.int8.onnx",
                "0be9702c2f427a2b6bb241d298e0d3836a558de1f5b9fd3018f1cce6e2b3fa98",
            ),
            _hf(
                _NEMOTRON_560,
                "joiner.int8.onnx",
                "a35eac38a22ebceb04d230ed7afe0d68f446ba6914a036b97f14fece95967e23",
            ),
            _hf(
                _NEMOTRON_560,
                "tokens.txt",
                "dc0b4584ab2e4ddbf888425c076c61b736e7356a015250db7d307e6f1a8188ff",
                8952,
            ),
        ),
    ),
    Asset(
        id="moonshine-v2-base-en",
        kind="asr",
        title="Moonshine v2 base English (quantized, sherpa-onnx)",
        license="MIT (see LICENSE in the archive)",
        source="https://github.com/moonshine-ai/moonshine",
        extract=True,
        files=(
            _gh_asr(
                "sherpa-onnx-moonshine-base-en-quantized-2026-02-27.tar.bz2",
                "43232c1d13013d37317163baec3135bd771a186a4356f28c889bab453bb0e891",
            ),
        ),
    ),
    Asset(
        id="moonshine-v2-tiny-en",
        kind="asr",
        title="Moonshine v2 tiny English (quantized, sherpa-onnx)",
        license="MIT (see LICENSE in the archive)",
        source="https://github.com/moonshine-ai/moonshine",
        extract=True,
        files=(
            _gh_asr(
                "sherpa-onnx-moonshine-tiny-en-quantized-2026-02-27.tar.bz2",
                "9ec31b342d8fa3240c3b81b8f82e1cf7e3ac467c93ca5a999b741d5887164f8d",
            ),
        ),
    ),
    Asset(
        id="kroko-en-streaming",
        kind="asr",
        title="Kroko streaming Zipformer English (sherpa-onnx)",
        license="see model card",
        source="https://huggingface.co/Banafo/Kroko-ASR",
        extract=True,
        files=(
            _gh_asr(
                "sherpa-onnx-streaming-zipformer-en-kroko-2025-08-06.tar.bz2",
                "c8676e5ff9ac2a85296e53ee0fd4d5fb1db6770e7a7647166eeafe349ade6834",
            ),
        ),
    ),
    Asset(
        id="lfm2.5-1.2b-instruct-q4km",
        kind="llm",
        title="Liquid LFM2.5 1.2B Instruct (GGUF Q4_K_M)",
        license="LFM Open License v1.0 (free for individuals / orgs < $10M revenue)",
        source="https://huggingface.co/LiquidAI/LFM2.5-1.2B-Instruct-GGUF",
        default=True,
        files=(
            _hf(
                "LiquidAI/LFM2.5-1.2B-Instruct-GGUF",
                "LFM2.5-1.2B-Instruct-Q4_K_M.gguf",
                "b1b3de114215d9507409a662a501a631095a479a419584e8a2ded6304b19b4f5",
                730_895_168,
            ),
        ),
    ),
    Asset(
        id="qwen3.5-0.8b-q4km",
        kind="llm",
        title="Qwen3.5 0.8B (GGUF Q4_K_M, Apache-2.0) - smaller/faster alternative",
        license="Apache-2.0",
        source="https://huggingface.co/unsloth/Qwen3.5-0.8B-GGUF",
        files=(
            _hf(
                "unsloth/Qwen3.5-0.8B-GGUF",
                "Qwen3.5-0.8B-Q4_K_M.gguf",
                "bd258782e35f7f458f8aced1adc053e6e92e89bc735ba3be89d38a06121dc517",
                532_517_120,
            ),
        ),
    ),
    Asset(
        id="qwen3.5-2b-q4km",
        kind="llm",
        title="Qwen3.5 2B (GGUF Q4_K_M, Apache-2.0) - higher quality, slower",
        license="Apache-2.0",
        source="https://huggingface.co/unsloth/Qwen3.5-2B-GGUF",
        files=(
            _hf(
                "unsloth/Qwen3.5-2B-GGUF",
                "Qwen3.5-2B-Q4_K_M.gguf",
                "aaf42c8b7c3cab2bf3d69c355048d4a0ee9973d48f16c731c0520ee914699223",
                1_280_835_840,
            ),
        ),
    ),
    Asset(
        id="llama.cpp-b11398-cpu-x64",
        kind="runtime",
        title="llama.cpp b11398 Windows CPU x64 (llama-server)",
        license="MIT",
        source="https://github.com/ggml-org/llama.cpp/releases/tag/b11398",
        default=True,
        extract=True,
        files=(
            RemoteFile(
                "https://github.com/ggml-org/llama.cpp/releases/download/b11398/"
                "llama-b11398-bin-win-cpu-x64.zip",
                "llama-b11398-bin-win-cpu-x64.zip",
                "a5b932cb28b2f17da93bf7111c7cbd1324cffa3a6f474aa10b616908868037a2",
                19_386_495,
            ),
        ),
    ),
    Asset(
        id="piper-en-lessac-medium",
        kind="tts",
        title="Piper en_US lessac medium (sherpa-onnx TTS) - only for synthetic test audio",
        license="MIT (voice: see model card)",
        source="https://github.com/k2-fsa/sherpa-onnx/releases/tag/tts-models",
        extract=True,
        files=(
            RemoteFile(
                "https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/"
                "vits-piper-en_US-lessac-medium.tar.bz2",
                "vits-piper-en_US-lessac-medium.tar.bz2",
                "9e3febfacf0abf4270172d2958bcec246032b7e88efc2720840cc80c93de334e",
            ),
        ),
    ),
)


def get_asset(asset_id: str) -> Asset:
    for asset in REGISTRY:
        if asset.id == asset_id:
            return asset
    known = ", ".join(a.id for a in REGISTRY)
    raise KeyError(f"unknown asset {asset_id!r} (known: {known})")


class VerificationError(RuntimeError):
    pass


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _download(rf: RemoteFile, target: Path, progress: ProgressFn | None) -> None:
    """Download ``rf`` to ``target`` (resumable), verifying the SHA-256."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file() and _sha256_file(target) == rf.sha256:
        return
    part = target.with_name(target.name + ".part")
    offset = part.stat().st_size if part.exists() else 0
    headers = {"User-Agent": _USER_AGENT}
    if offset:
        headers["Range"] = f"bytes={offset}-"
    request = urllib.request.Request(rf.url, headers=headers)
    try:
        response = urllib.request.urlopen(request, timeout=60)
    except urllib.error.HTTPError as exc:
        if exc.code == 416 and offset:  # range not satisfiable: restart cleanly
            part.unlink()
            _download(rf, target, progress)
            return
        raise
    with response:
        if offset and response.status != 206:  # server ignored the range
            offset = 0
        length = response.headers.get("Content-Length")
        total = (int(length) + offset) if length is not None else rf.size
        mode = "ab" if offset else "wb"
        done = offset
        last_report = 0.0
        with part.open(mode) as out:
            while chunk := response.read(_CHUNK):
                out.write(chunk)
                done += len(chunk)
                now = time.monotonic()
                if progress and now - last_report > 1.0:
                    progress(rf.dest, done, total)
                    last_report = now
    if progress:
        progress(rf.dest, done, total)
    actual = _sha256_file(part)
    if actual != rf.sha256:
        part.unlink(missing_ok=True)
        raise VerificationError(
            f"SHA-256 mismatch for {rf.dest}: expected {rf.sha256}, got {actual}"
        )
    os.replace(part, target)


def _safe_members(names: Iterable[str]) -> None:
    for name in names:
        pure = PurePosixPath(name.replace("\\", "/"))
        if pure.is_absolute() or ".." in pure.parts:
            raise VerificationError(f"refusing to extract unsafe archive member: {name}")


def _extract(archive: Path, dest: Path) -> None:
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as zf:
            _safe_members(zf.namelist())
            zf.extractall(dest)
    else:
        with tarfile.open(archive) as tf:
            _safe_members(tf.getnames())
            tf.extractall(dest, filter="data")
        # tarballs wrap content in one top-level folder: flatten it.
        children = [p for p in dest.iterdir() if p != archive and p.name != _MARKER]
        if len(children) == 1 and children[0].is_dir():
            inner = children[0]
            for item in inner.iterdir():
                shutil.move(str(item), dest / item.name)
            inner.rmdir()
    archive.unlink()


def install(asset: Asset, paths: Paths | None = None, progress: ProgressFn | None = None) -> Path:
    """Download (if needed), verify and unpack an asset. Returns its directory."""
    paths = paths or get_paths()
    directory = asset.directory(paths)
    if asset.is_installed(paths):
        return directory
    directory.mkdir(parents=True, exist_ok=True)
    for rf in asset.files:
        target = directory / PurePosixPath(rf.dest)
        _download(rf, target, progress)
        if asset.extract:
            _extract(target, directory)
    marker = {
        "id": asset.id,
        "files": {rf.dest: rf.sha256 for rf in asset.files},
        "installed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    (directory / _MARKER).write_text(json.dumps(marker, indent=2), encoding="utf-8")
    return directory


def remove(asset: Asset, paths: Paths | None = None) -> None:
    directory = asset.directory(paths or get_paths())
    if directory.exists():
        shutil.rmtree(directory)


def find_file(directory: Path, name: str) -> Path:
    """Locate ``name`` anywhere under ``directory`` (archives vary in layout)."""
    direct = directory / name
    if direct.exists():
        return direct
    for candidate in directory.rglob(name):
        return candidate
    raise FileNotFoundError(f"{name} not found under {directory}")
