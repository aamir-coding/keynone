"""Starts the engine process on demand and talks to it over its stdin/stdout."""

from __future__ import annotations

import contextlib
import logging
import os
import queue
import subprocess
import sys
import threading
from collections.abc import Callable
from typing import Any

from keynone.ipc import Msg, encode, encode_json, parse_event
from keynone.paths import Paths
from keynone.win32.process import KillOnCloseJob

log = logging.getLogger(__name__)

EventCallback = Callable[[dict[str, Any]], None]


class EngineProcess:
    def __init__(self, job: KillOnCloseJob, paths: Paths, on_event: EventCallback) -> None:
        self._job = job
        self._paths = paths
        self._on_event = on_event
        self._proc: subprocess.Popen[bytes] | None = None
        self._outbox: queue.Queue[bytes | None] = queue.Queue()
        self._lock = threading.Lock()

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def ensure_started(self) -> bool:
        """Start the engine if needed; returns True if a new process was started."""
        with self._lock:
            if self.alive:
                return False
            self._outbox = queue.Queue()
            stderr = (self._paths.logs / "engine-stderr.log").open("ab")
            env = {**os.environ, "KEYNONE_HOME": str(self._paths.home)}
            try:
                self._proc = subprocess.Popen(
                    [sys.executable, "-m", "keynone", "engine"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=stderr,
                    cwd=str(self._paths.home),
                    env=env,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            finally:
                stderr.close()
            try:
                self._job.assign(self._proc.pid)
            except OSError:
                log.warning("could not assign engine to job object", exc_info=True)
            proc, outbox = self._proc, self._outbox
            threading.Thread(target=self._write_loop, args=(proc, outbox), name="engine-in",
                             daemon=True).start()
            threading.Thread(target=self._read_loop, args=(proc,), name="engine-out",
                             daemon=True).start()
            log.info("engine started (pid %s)", proc.pid)
            return True

    def send(self, kind: Msg, payload: bytes = b"") -> None:
        if self.alive:
            self._outbox.put(encode(kind, payload))

    def send_json(self, kind: Msg, obj: dict[str, Any]) -> None:
        if self.alive:
            self._outbox.put(encode_json(kind, obj))

    def quit(self, wait: float = 5.0) -> None:
        with self._lock:
            proc, self._proc = self._proc, None
            outbox = self._outbox
        if proc is None or proc.poll() is not None:
            return
        outbox.put(encode(Msg.QUIT))
        outbox.put(None)
        try:
            proc.wait(timeout=wait)
        except subprocess.TimeoutExpired:
            log.warning("engine did not exit; killing it")
            proc.kill()

    def _write_loop(self, proc: subprocess.Popen[bytes], outbox: queue.Queue[bytes | None]) -> None:
        assert proc.stdin is not None
        while True:
            data = outbox.get()
            if data is None:
                break
            try:
                proc.stdin.write(data)
                proc.stdin.flush()
            except (OSError, ValueError):
                break
        with contextlib.suppress(OSError):
            proc.stdin.close()

    def _read_loop(self, proc: subprocess.Popen[bytes]) -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            event = parse_event(line)
            if event is not None:
                self._on_event(event)
        code = proc.wait()
        self._on_event({"event": "exited", "code": code, "pid": proc.pid})
