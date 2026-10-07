"""Local LLM via a private ``llama-server`` child process.

Security: the server binds to 127.0.0.1 on a random free port and requires a
random per-run API key, so other local processes cannot use it. It is placed
in a kill-on-close job object, so it dies with Keynone even after a crash.

Latency: the fixed prompt prefix (system prompt + examples) is processed once
and reused for every request (llama-server prompt cache, with an explicit
slot save/restore fallback for hybrid models whose state cannot be rolled
back), so each request only pays for the new transcript.
"""

from __future__ import annotations

import contextlib
import dataclasses
import hashlib
import http.client
import json
import logging
import os
import re
import secrets
import socket
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from keynone.assets import find_file, get_asset
from keynone.cleanup.prompts import (
    REWRITE_STOP,
    PromptBuilder,
    PromptStyle,
    PromptVariant,
    TemplateFamily,
    rewrite_prefix,
    rewrite_prompt,
)
from keynone.paths import Paths

log = logging.getLogger(__name__)

LLAMA_RUNTIME_ID = "llama.cpp-b11398-cpu-x64"
LLAMA_VULKAN_RUNTIME_ID = "llama.cpp-b11398-vulkan-x64"

# Chat-template family per registered model id.
MODEL_FAMILIES: dict[str, TemplateFamily] = {
    "lfm2.5-1.2b-instruct-q4km": "chatml",
    "qwen3.5-0.8b-q4km": "qwen",
    "qwen3.5-2b-q4km": "qwen",
}


def llm_model_path(model_id: str, paths: Paths) -> Path:
    asset = get_asset(model_id)
    if asset.kind != "llm":
        raise LlmError(f"{model_id} is not an LLM asset")
    return asset.directory(paths) / asset.files[0].dest


def llama_server_exe(paths: Paths) -> Path:
    return find_file(get_asset(LLAMA_RUNTIME_ID).directory(paths), "llama-server.exe")


class LlmError(RuntimeError):
    pass


@dataclass
class Completion:
    text: str
    prompt_tokens: int = 0  # tokens actually evaluated (cache misses)
    cached_tokens: int = 0
    generated_tokens: int = 0
    prompt_ms: float = 0.0
    generate_ms: float = 0.0
    total_ms: float = 0.0
    truncated: bool = False  # hit the deadline before finishing


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@dataclass
class LlamaServer:
    """A private llama-server. Slot 0 serves cleanup (cached prompt prefix),
    slot 1 serves rewrites, so a rewrite never evicts the cleanup cache."""

    exe: Path
    model: Path
    threads: int = 4
    context: int = 4096  # per slot
    parallel: int = 2
    slot_dir: Path | None = None
    log_file: Path | None = None
    gpu_device: str | None = None  # e.g. "Vulkan0"; None = CPU only
    gpu_layers: int = 99
    extra_args: list[str] = field(default_factory=list)

    _proc: subprocess.Popen[bytes] | None = field(default=None, init=False, repr=False)
    _port: int = field(default=0, init=False)
    _key: str = field(default="", init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    # ----------------------------------------------------------------- lifecycle

    def start(self, job: Any | None = None) -> None:
        """Launch the server (non-blocking); see :meth:`wait_ready`."""
        if self._proc is not None:
            return
        if not self.exe.is_file():
            raise LlmError(f"llama-server not found: {self.exe} (run `keynone setup`)")
        if not self.model.is_file():
            raise LlmError(f"LLM model not found: {self.model} (run `keynone setup`)")
        self._port = _free_port()
        self._key = secrets.token_urlsafe(24)
        args = [
            str(self.exe),
            "--model", str(self.model),
            "--host", "127.0.0.1",
            "--port", str(self._port),
            "--api-key", self._key,
            "--threads", str(self.threads),
            "--threads-batch", str(self.threads),
            "--ctx-size", str(self.context * self.parallel),
            "--parallel", str(self.parallel),
            "--no-webui",
            "--cache-reuse", "0",
            *(["--slot-save-path", str(self.slot_dir)] if self.slot_dir else []),
            # CPU mode uses the CPU-only build, so device flags only matter for GPU.
            *(["--device", self.gpu_device, "--n-gpu-layers", str(self.gpu_layers)]
              if self.gpu_device else []),
            *self.extra_args,
        ]
        if self.slot_dir:
            self.slot_dir.mkdir(parents=True, exist_ok=True)
        stdout: Any = subprocess.DEVNULL
        if self.log_file:
            self.log_file.parent.mkdir(parents=True, exist_ok=True)
            stdout = self.log_file.open("ab")
        try:
            self._proc = subprocess.Popen(
                args,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW,
                cwd=str(self.exe.parent),
            )
        finally:
            if stdout is not subprocess.DEVNULL:
                stdout.close()
        if job is not None:
            try:
                job.assign(self._proc.pid)
            except OSError:
                log.warning("could not assign llama-server to job object", exc_info=True)
        log.info("llama-server starting (pid %s, port %s)", self._proc.pid, self._port)

    def wait_ready(self, timeout: float = 30.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._proc is None or self._proc.poll() is not None:
                return False
            try:
                status, _ = self._request("GET", "/health", timeout=1.0)
                if status == 200:
                    return True
            except OSError:
                pass
            time.sleep(0.05)
        return False

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def close(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()

    # ----------------------------------------------------------------- HTTP

    def _connection(self, timeout: float) -> http.client.HTTPConnection:
        return http.client.HTTPConnection("127.0.0.1", self._port, timeout=timeout)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"}

    def _request(
        self, method: str, path: str, body: dict[str, Any] | None = None, timeout: float = 10.0
    ) -> tuple[int, Any]:
        conn = self._connection(timeout)
        try:
            payload = json.dumps(body).encode() if body is not None else None
            conn.request(method, path, body=payload, headers=self._headers())
            response = conn.getresponse()
            raw = response.read()
            try:
                data = json.loads(raw) if raw else None
            except json.JSONDecodeError:
                data = raw.decode(errors="replace")
            return response.status, data
        finally:
            conn.close()

    def tokenize(self, text: str) -> list[int]:
        status, data = self._request("POST", "/tokenize", {"content": text, "add_special": True})
        if status != 200:
            raise LlmError(f"tokenize failed: {status} {data}")
        return list(data["tokens"])

    def slot_action(self, action: str, filename: str, slot: int = 0) -> bool:
        status, data = self._request(
            "POST", f"/slots/{slot}?action={action}", {"filename": filename}, timeout=30.0
        )
        if status != 200:
            log.debug("slot %s failed: %s %s", action, status, data)
        return status == 200

    def complete(
        self,
        prompt: str,
        *,
        max_tokens: int,
        stop: list[str],
        deadline: float | None = None,
        on_text: Callable[[str], None] | None = None,
        slot: int = 0,
    ) -> Completion:
        """Greedy completion streamed over SSE; stops early at ``deadline``."""
        body = {
            "prompt": prompt,
            "n_predict": max_tokens,
            "temperature": 0.0,
            "top_k": 1,
            "cache_prompt": True,
            "id_slot": slot,
            "stop": stop,
            "stream": True,
            "timings_per_token": False,
        }
        started = time.monotonic()
        remaining = 60.0 if deadline is None else max(0.05, deadline - started)
        conn = self._connection(timeout=min(remaining, 60.0))
        result = Completion(text="")
        pieces: list[str] = []
        try:
            conn.request("POST", "/completion", body=json.dumps(body), headers=self._headers())
            response = conn.getresponse()
            if response.status != 200:
                raise LlmError(f"completion failed: HTTP {response.status} {response.read()!r}")
            while True:
                if deadline is not None and time.monotonic() > deadline:
                    result.truncated = True
                    break
                try:
                    line = response.readline()
                except TimeoutError:
                    result.truncated = True
                    break
                if not line:
                    break
                if not line.startswith(b"data: "):
                    continue
                event = json.loads(line[6:])
                piece = event.get("content", "")
                if piece:
                    pieces.append(piece)
                    if on_text:
                        on_text("".join(pieces))
                if event.get("stop"):
                    timings = event.get("timings") or {}
                    result.prompt_tokens = int(timings.get("prompt_n", 0))
                    result.cached_tokens = int(timings.get("cache_n", 0))
                    result.generated_tokens = int(timings.get("predicted_n", 0))
                    result.prompt_ms = float(timings.get("prompt_ms", 0.0))
                    result.generate_ms = float(timings.get("predicted_ms", 0.0))
                    break
        except (OSError, http.client.HTTPException) as exc:
            if deadline is not None and time.monotonic() > deadline:
                result.truncated = True
            else:
                raise LlmError(f"completion failed: {exc}") from exc
        finally:
            conn.close()  # on early exit this also makes the server cancel the task
        result.text = "".join(pieces)
        result.total_ms = (time.monotonic() - started) * 1000
        return result


CLEANUP_SLOT, REWRITE_SLOT = 0, 1
# Saved prompt caches (data/cache/llm-slots): every change of model, prompt or
# dictionary words saves a new 3-25 MB file. Keep the recently used ones only.
SLOT_KEEP_DAYS = 14
SLOT_KEEP_FILES = 8


class LlmPolisher:
    """Cleans, translates and rewrites text with the local LLM.

    Cleanup requests share a fixed prompt prefix per variant (English,
    multilingual, translation); the prefix's evaluated state is saved to disk
    once and restored into slot 0 when needed, so each request only pays for
    the new transcript. Rewrites use slot 1. Thread-safe (one request at a time).
    """

    def __init__(self, server: LlamaServer, builder: PromptBuilder) -> None:
        self.server = server
        self.builder = builder
        self._lock = threading.Lock()
        self._builders: dict[tuple[str, str], PromptBuilder] = {}
        self._loaded_prefix: str | None = None  # prefix file currently in slot 0
        self._use_slot_restore = False

    def _builder_for(self, variant: PromptVariant, target: str) -> PromptBuilder:
        key = (variant, target if variant == "translate" else "")
        if key not in self._builders:
            self._builders[key] = dataclasses.replace(
                self.builder, variant=variant, target_language=target or "English"
            )
        return self._builders[key]

    def _prefix_name(self, builder: PromptBuilder) -> str:
        identity = (f"{self.server.model.name}|{builder.family}|{self.server.context}|"
                    f"{self.server.gpu_device}|{builder.prefix()}")
        return f"prefix-{hashlib.sha256(identity.encode()).hexdigest()[:16]}.bin"

    def _touch(self, name: str) -> None:
        """Mark a saved prompt cache as used (pruning goes by the file's time)."""
        if self.server.slot_dir:
            with contextlib.suppress(OSError):
                os.utime(self.server.slot_dir / name)

    def prune_slot_files(self, *, keep_days: float = SLOT_KEEP_DAYS,
                         keep_files: int = SLOT_KEEP_FILES) -> int:
        """Delete saved prompt caches unused for ``keep_days``, and any beyond the
        ``keep_files`` most recently used; never the ones loaded now. A deleted cache
        costs one prompt evaluation the next time it is needed, nothing else."""
        directory = self.server.slot_dir
        if directory is None or not directory.is_dir():
            return 0
        in_use = {self._loaded_prefix, self._rewrite_prefix_name()}
        files: list[tuple[float, Path]] = []
        for path in directory.glob("*.bin"):
            if path.name.startswith(("prefix-", "rewrite-")) and path.name not in in_use:
                with contextlib.suppress(OSError):
                    files.append((path.stat().st_mtime, path))
        files.sort(reverse=True)  # most recently used first
        cutoff = time.time() - keep_days * 86_400
        keep = keep_files - len(in_use - {None})
        deleted = 0
        for index, (used, path) in enumerate(files):
            if index < keep and used >= cutoff:
                continue
            with contextlib.suppress(OSError):
                path.unlink()
                deleted += 1
        if deleted:
            log.info("deleted %d unused prompt cache file(s)", deleted)
        return deleted

    def _load_prefix_locked(self, builder: PromptBuilder) -> None:
        name = self._prefix_name(builder)
        if self._loaded_prefix == name and not self._use_slot_restore:
            return
        restored = False
        if self.server.slot_dir and (self.server.slot_dir / name).is_file():
            restored = self.server.slot_action("restore", name, CLEANUP_SLOT)
            if restored:
                self._touch(name)
        if not restored:
            self.server.complete(builder.prefix(), max_tokens=0, stop=[], slot=CLEANUP_SLOT)
            if self.server.slot_dir:
                self.server.slot_action("save", name, CLEANUP_SLOT)
        self._loaded_prefix = name

    def prime(self, variant: PromptVariant = "en", target: str = "English") -> None:
        """Evaluate (or restore) a prefix so the first request is fast."""
        with self._lock:
            self._load_prefix_locked(self._builder_for(variant, target))

    def polish(
        self,
        transcript: str,
        *,
        variant: PromptVariant = "en",
        target: str = "English",
        deadline: float | None = None,
        on_text: Callable[[str], None] | None = None,
    ) -> Completion:
        builder = self._builder_for(variant, target)
        with self._lock:
            already_loaded = self._loaded_prefix == self._prefix_name(builder)
            self._load_prefix_locked(builder)
            if variant == "en":
                max_tokens = 48 + int(len(transcript.split()) * 2.2)
            else:  # scripts like Devanagari/CJK need more tokens per word
                max_tokens = min(1024, 64 + int(len(transcript) * 1.5))
            result = self.server.complete(
                builder.request(transcript), max_tokens=max_tokens, stop=builder.stop,
                deadline=deadline, on_text=on_text, slot=CLEANUP_SLOT,
            )
            # Hybrid/recurrent models cannot roll their cache back to the shared
            # prefix: detect it and restore the saved prefix before each request.
            if (
                already_loaded
                and not self._use_slot_restore
                and self.server.slot_dir
                and result.prompt_tokens > len(transcript) // 2 + 96
            ):
                log.info("prompt cache miss (%d tokens); switching to slot restore",
                         result.prompt_tokens)
                self._use_slot_restore = True
            return result

    def warm_up_generation(self) -> None:
        """One tiny generation on the rewrite slot (GPU kernels compile on first use);
        the cached cleanup prefix in slot 0 is left untouched."""
        with self._lock, contextlib.suppress(LlmError):
            self.server.complete(rewrite_prompt(self.builder.family, "fix typos", "helo"),
                                 max_tokens=4, stop=REWRITE_STOP, slot=REWRITE_SLOT)

    def _rewrite_prefix_name(self) -> str:
        identity = (f"{self.server.model.name}|{self.server.context}|{self.server.gpu_device}|"
                    f"{rewrite_prefix()}")
        return f"rewrite-{hashlib.sha256(identity.encode()).hexdigest()[:16]}.bin"

    def _load_rewrite_prefix_locked(self) -> None:
        """Put the evaluated rewrite instructions/examples into slot 1 (saved once,
        restored before every rewrite: hybrid models can't reuse a partial cache)."""
        if not self.server.slot_dir:
            return
        name = self._rewrite_prefix_name()
        if (self.server.slot_dir / name).is_file() and self.server.slot_action(
                "restore", name, REWRITE_SLOT):
            self._touch(name)
            return
        self.server.complete(rewrite_prefix(), max_tokens=0, stop=[], slot=REWRITE_SLOT)
        self.server.slot_action("save", name, REWRITE_SLOT)

    def prime_rewrite(self) -> None:
        """Evaluate (or restore) the rewrite prefix so the first rewrite is fast."""
        with self._lock, contextlib.suppress(LlmError):
            self._load_rewrite_prefix_locked()

    def rewrite(
        self,
        instruction: str,
        text: str,
        *,
        deadline: float | None = None,
        on_text: Callable[[str], None] | None = None,
    ) -> Completion:
        """Edit ``text`` according to a spoken ``instruction`` (speak-to-edit)."""
        prompt = rewrite_prompt(self.builder.family, instruction, text)
        budget = self.server.context - (len(prompt) // 3) - 64
        max_tokens = max(64, min(budget, 200 + len(text)))
        with self._lock:
            with contextlib.suppress(LlmError):
                self._load_rewrite_prefix_locked()
            return self.server.complete(prompt, max_tokens=max_tokens, stop=REWRITE_STOP,
                                        deadline=deadline, on_text=on_text, slot=REWRITE_SLOT)

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self.server.close()


@dataclass(frozen=True)
class GpuDevice:
    name: str  # llama.cpp device id, e.g. "Vulkan0"
    description: str  # e.g. "NVIDIA GeForce GTX 1650"
    memory_mb: int

    @property
    def discrete(self) -> bool:
        lowered = self.description.lower()
        return any(k in lowered for k in ("nvidia", "geforce", "rtx", "quadro", "radeon rx",
                                          "arc a", "arc b"))


_DEVICE_LINE_RE = re.compile(r"^\s*(\w+):\s*(.+?)\s*\((\d+)\s*MiB")


def list_gpu_devices(exe: Path, timeout: float = 30.0) -> list[GpuDevice]:
    """GPUs llama.cpp can use (``llama-server --list-devices``)."""
    try:
        out = subprocess.run([str(exe), "--list-devices"], capture_output=True, text=True,
                             timeout=timeout, creationflags=subprocess.CREATE_NO_WINDOW,
                             cwd=str(exe.parent), check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.warning("could not list GPU devices: %s", exc)
        return []
    devices = []
    for line in (out.stdout + out.stderr).splitlines():
        m = _DEVICE_LINE_RE.match(line)
        if m:
            devices.append(GpuDevice(m.group(1), m.group(2), int(m.group(3))))
    return devices


def choose_gpu_device(devices: list[GpuDevice], preference: str) -> GpuDevice | None:
    """``auto``: the discrete GPU with the most memory (else the largest); otherwise
    the first device whose id or name contains ``preference``."""
    if not devices:
        return None
    if preference.lower() == "auto":
        return max(devices, key=lambda d: (d.discrete, d.memory_mb))
    wanted = preference.lower()
    return next((d for d in devices if wanted in d.name.lower() or wanted in
                 d.description.lower()), None)


def resolve_gpu_device(preference: str, paths: Paths) -> tuple[Path, GpuDevice] | None:
    """Pick the Vulkan llama-server and device for ``llm.gpu``; ``None`` = use CPU."""
    if preference.lower() in ("", "off", "none", "cpu"):
        return None
    runtime = get_asset(LLAMA_VULKAN_RUNTIME_ID)
    if not runtime.is_installed(paths):
        log.warning("llm.gpu is set but the GPU runtime is missing: keynone setup --with %s",
                    LLAMA_VULKAN_RUNTIME_ID)
        return None
    exe = find_file(runtime.directory(paths), "llama-server.exe")
    devices = available_gpu_devices(paths, exe)
    device = choose_gpu_device(devices, preference)
    if device is None:
        log.warning("no GPU matching %r (found: %s); using the CPU", preference,
                    ", ".join(d.description for d in devices) or "none")
        return None
    return exe, device


def available_gpu_devices(paths: Paths, exe: Path | None = None) -> list[GpuDevice]:
    """GPUs the Vulkan runtime can use ([] without it); cached until the runtime changes."""
    if exe is None:
        runtime = get_asset(LLAMA_VULKAN_RUNTIME_ID)
        if not runtime.is_installed(paths):
            return []
        exe = find_file(runtime.directory(paths), "llama-server.exe")
    cache = paths.cache / "gpu-devices.json"
    stamp = f"{exe}|{exe.stat().st_mtime_ns}"
    devices: list[GpuDevice] = []
    try:
        cached = json.loads(cache.read_text(encoding="utf-8"))
        if cached.get("stamp") == stamp:
            devices = [GpuDevice(**d) for d in cached["devices"]]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    if not devices:
        devices = list_gpu_devices(exe)
        with contextlib.suppress(OSError):
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps({"stamp": stamp,
                                         "devices": [vars(d) for d in devices]}),
                             encoding="utf-8")
    return devices


def build_polisher(
    model_id: str,
    paths: Paths,
    *,
    threads: int = 4,
    context: int = 4096,
    dictionary_words: tuple[str, ...] = (),
    style: PromptStyle = "doc",
    gpu: str = "off",
    gpu_layers: int = 99,
    job: Any | None = None,
) -> LlmPolisher:
    """Create and start (non-blocking) a polisher for a registered model."""
    exe, device = llama_server_exe(paths), None
    selected = resolve_gpu_device(gpu, paths)
    if selected is not None:
        exe, gpu_choice = selected
        device = gpu_choice.name
        log.info("LLM on GPU: %s (%s, %d MiB)", gpu_choice.name, gpu_choice.description,
                 gpu_choice.memory_mb)
    server = LlamaServer(
        exe=exe,
        model=llm_model_path(model_id, paths),
        threads=threads,
        context=context,
        slot_dir=paths.cache / "llm-slots",
        log_file=paths.logs / "llama-server.log",
        gpu_device=device,
        gpu_layers=gpu_layers,
    )
    server.start(job)
    builder = PromptBuilder(
        family=MODEL_FAMILIES.get(model_id, "chatml"),
        style=style,
        dictionary_words=dictionary_words,
    )
    return LlmPolisher(server, builder)
