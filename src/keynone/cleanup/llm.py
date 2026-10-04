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
import hashlib
import http.client
import json
import logging
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
from keynone.cleanup.prompts import PromptBuilder, PromptStyle, TemplateFamily
from keynone.paths import Paths

log = logging.getLogger(__name__)

LLAMA_RUNTIME_ID = "llama.cpp-b11398-cpu-x64"

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
    exe: Path
    model: Path
    threads: int = 4
    context: int = 2048
    slot_dir: Path | None = None
    log_file: Path | None = None
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
            "--ctx-size", str(self.context),
            "--parallel", "1",
            "--no-webui",
            "--cache-reuse", "0",
            *(["--slot-save-path", str(self.slot_dir)] if self.slot_dir else []),
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

    def slot_action(self, action: str, filename: str) -> bool:
        status, data = self._request(
            "POST", f"/slots/0?action={action}", {"filename": filename}, timeout=30.0
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
    ) -> Completion:
        """Greedy completion streamed over SSE; stops early at ``deadline``."""
        body = {
            "prompt": prompt,
            "n_predict": max_tokens,
            "temperature": 0.0,
            "top_k": 1,
            "cache_prompt": True,
            "id_slot": 0,
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


class LlmPolisher:
    """Cleans transcripts with the local LLM; thread-safe (one request at a time)."""

    def __init__(self, server: LlamaServer, builder: PromptBuilder) -> None:
        self.server = server
        self.builder = builder
        self._lock = threading.Lock()
        self._prefix_file = ""
        self._use_slot_restore = False
        self._primed = False

    def _prefix_name(self) -> str:
        digest = hashlib.sha256(
            f"{self.server.model.name}|{self.builder.family}|{self.builder.prefix()}".encode()
        ).hexdigest()[:16]
        return f"prefix-{digest}.bin"

    def prime(self) -> None:
        """Evaluate (or restore) the fixed prefix so the first request is fast."""
        with self._lock:
            self._prime_locked()

    def _prime_locked(self) -> None:
        if self._primed:
            return
        self._prefix_file = self._prefix_name()
        restored = False
        if self.server.slot_dir and (self.server.slot_dir / self._prefix_file).is_file():
            restored = self.server.slot_action("restore", self._prefix_file)
        if not restored:
            self.server.complete(self.builder.prefix(), max_tokens=0, stop=[])
            if self.server.slot_dir:
                self.server.slot_action("save", self._prefix_file)
        self._primed = True

    def polish(
        self,
        transcript: str,
        *,
        deadline: float | None = None,
        on_text: Callable[[str], None] | None = None,
    ) -> Completion:
        with self._lock:
            self._prime_locked()
            if self._use_slot_restore:
                self.server.slot_action("restore", self._prefix_file)
            prompt = self.builder.request(transcript)
            max_tokens = 48 + int(len(transcript.split()) * 2.2)
            result = self.server.complete(
                prompt, max_tokens=max_tokens, stop=self.builder.stop, deadline=deadline,
                on_text=on_text,
            )
            # Hybrid/recurrent models cannot roll back their cache to the shared
            # prefix; detect that and restore the saved prefix state instead.
            if (
                not self._use_slot_restore
                and self.server.slot_dir
                and result.prompt_tokens > len(transcript.split()) * 3 + 64
            ):
                log.info("prompt cache miss (%d tokens); switching to slot restore",
                         result.prompt_tokens)
                self._use_slot_restore = True
            return result

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self.server.close()


def build_polisher(
    model_id: str,
    paths: Paths,
    *,
    threads: int = 4,
    context: int = 2048,
    dictionary_words: tuple[str, ...] = (),
    style: PromptStyle = "doc",
    job: Any | None = None,
) -> LlmPolisher:
    """Create and start (non-blocking) a polisher for a registered model."""
    server = LlamaServer(
        exe=llama_server_exe(paths),
        model=llm_model_path(model_id, paths),
        threads=threads,
        context=context,
        slot_dir=paths.cache / "llm-slots",
        log_file=paths.logs / "llama-server.log",
    )
    server.start(job)
    builder = PromptBuilder(
        family=MODEL_FAMILIES.get(model_id, "chatml"),
        style=style,
        dictionary_words=dictionary_words,
    )
    return LlmPolisher(server, builder)
