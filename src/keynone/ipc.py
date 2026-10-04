"""Listener <-> engine protocol.

listener -> engine (engine stdin), binary frames::

    frame := kind:u8 | length:u32 little-endian | payload[length]

engine -> listener (engine stdout): one JSON object per line, ``{"event": ...}``.
"""

from __future__ import annotations

import json
import struct
import threading
from enum import IntEnum
from typing import Any, BinaryIO

_HEADER = struct.Struct("<BI")
MAX_PAYLOAD = 16 * 1024 * 1024


class Msg(IntEnum):
    START = ord("S")  # JSON session metadata
    AUDIO = ord("A")  # 16 kHz mono PCM16
    STOP = ord("E")  # user finished: finalize and insert
    CANCEL = ord("X")  # discard the current dictation
    MODE = ord("M")  # JSON {"mode": "handsfree"}: double-tap switched modes
    QUIT = ord("Q")  # exit the engine


class ProtocolError(RuntimeError):
    pass


def encode(kind: Msg, payload: bytes = b"") -> bytes:
    return _HEADER.pack(int(kind), len(payload)) + payload


def encode_json(kind: Msg, obj: dict[str, Any]) -> bytes:
    return encode(kind, json.dumps(obj, separators=(",", ":")).encode())


def _read_exact(stream: BinaryIO, size: int) -> bytes | None:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            return None
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def read_frame(stream: BinaryIO) -> tuple[Msg, bytes] | None:
    """Read one frame; ``None`` on a clean EOF."""
    header = _read_exact(stream, _HEADER.size)
    if header is None:
        return None
    kind, length = _HEADER.unpack(header)
    if length > MAX_PAYLOAD:
        raise ProtocolError(f"frame too large: {length} bytes")
    try:
        msg = Msg(kind)
    except ValueError as exc:
        raise ProtocolError(f"unknown frame kind {kind:#x}") from exc
    payload = _read_exact(stream, length) if length else b""
    if payload is None:
        raise ProtocolError("truncated frame")
    return msg, payload


class EventWriter:
    """Thread-safe JSON-lines writer for engine -> listener events."""

    def __init__(self, stream: BinaryIO | None) -> None:
        self._stream = stream
        self._lock = threading.Lock()

    def send(self, event: str, **fields: Any) -> None:
        if self._stream is None:
            return
        line = json.dumps({"event": event, **fields}, separators=(",", ":")) + "\n"
        with self._lock:
            try:
                self._stream.write(line.encode())
                self._stream.flush()
            except (OSError, ValueError):
                self._stream = None  # listener went away; keep running headless


def parse_event(line: bytes) -> dict[str, Any] | None:
    try:
        data = json.loads(line)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) and "event" in data else None
