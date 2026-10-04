"""Microphone capture via waveIn (MME) at 16 kHz mono PCM16.

MME opens in ~10-30 ms (PortAudio's device enumeration can take a second),
which is what lets recording begin the instant the hotkey is pressed. The
Windows audio engine resamples from the device's native format for us.
"""

from __future__ import annotations

import ctypes
import logging
import threading
from collections.abc import Callable
from typing import Any

from keynone.win32 import api

log = logging.getLogger(__name__)

WAVE_MAPPER = 0xFFFFFFFF
WAVE_FORMAT_PCM = 1
CALLBACK_EVENT = 0x00050000
WHDR_DONE = 0x00000001
MMSYSERR_NOERROR = 0
SAMPLE_RATE = 16_000


class AudioError(RuntimeError):
    pass


def _check(result: int, what: str) -> None:
    if result != MMSYSERR_NOERROR:
        buffer = ctypes.create_unicode_buffer(256)
        api.waveInGetErrorTextW(result, buffer, 256)
        raise AudioError(f"{what} failed: {buffer.value or result}")


def input_devices() -> list[str]:
    names = []
    for index in range(api.waveInGetNumDevs()):
        caps = api.WAVEINCAPSW()
        if api.waveInGetDevCapsW(index, ctypes.byref(caps), ctypes.sizeof(caps)) == 0:
            names.append(caps.szPname)
    return names


def _device_id(name: str) -> int:
    if not name:
        return WAVE_MAPPER
    for index, device in enumerate(input_devices()):
        if name.lower() in device.lower():
            return index
    log.warning("microphone %r not found; using the default device", name)
    return WAVE_MAPPER


class AudioCapture:
    """Delivers 20 ms PCM16 frames to ``on_audio`` from a background thread."""

    FRAME_BYTES = SAMPLE_RATE * 2 // 50  # 20 ms
    BUFFERS = 25  # 500 ms of headroom if the consumer stalls

    def __init__(self, on_audio: Callable[[bytes], None], device: str = "") -> None:
        self._on_audio = on_audio
        self._device = device
        self._stop = threading.Event()
        self._opened = threading.Event()
        self._error: Exception | None = None
        self._thread: threading.Thread | None = None

    def start(self, timeout: float = 2.0) -> None:
        self._thread = threading.Thread(target=self._run, name="audio", daemon=True)
        self._thread.start()
        if not self._opened.wait(timeout):
            raise AudioError("microphone did not open in time")
        if self._error:
            raise self._error

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def _run(self) -> None:
        event = api.CreateEventW(None, False, False, None)
        handle = api.HWAVEIN()
        fmt = api.WAVEFORMATEX(WAVE_FORMAT_PCM, 1, SAMPLE_RATE, SAMPLE_RATE * 2, 2, 16, 0)
        headers = (api.WAVEHDR * self.BUFFERS)()
        buffers = [ctypes.create_string_buffer(self.FRAME_BYTES) for _ in range(self.BUFFERS)]
        hdr_size = ctypes.sizeof(api.WAVEHDR)
        try:
            _check(api.waveInOpen(ctypes.byref(handle), _device_id(self._device),
                                  ctypes.byref(fmt), event, 0, CALLBACK_EVENT), "waveInOpen")
        except Exception as exc:
            self._error = exc
            self._opened.set()
            api.CloseHandle(event)
            return
        try:
            for header, buffer in zip(headers, buffers, strict=True):
                header.lpData = ctypes.cast(buffer, ctypes.c_void_p)
                header.dwBufferLength = self.FRAME_BYTES
                _check(api.waveInPrepareHeader(handle, ctypes.byref(header), hdr_size),
                       "waveInPrepareHeader")
                _check(api.waveInAddBuffer(handle, ctypes.byref(header), hdr_size),
                       "waveInAddBuffer")
            _check(api.waveInStart(handle), "waveInStart")
        except Exception as exc:
            self._error = exc
            self._opened.set()
            api.waveInReset(handle)
            api.waveInClose(handle)
            api.CloseHandle(event)
            return
        self._opened.set()
        index = 0
        try:
            while not self._stop.is_set():
                api.WaitForSingleObject(event, 50)
                index = self._drain(handle, headers, index, requeue=True)
            api.waveInReset(handle)  # returns the partially filled buffers
            self._drain(handle, headers, index, requeue=False)
        finally:
            for header in headers:
                api.waveInUnprepareHeader(handle, ctypes.byref(header), hdr_size)
            api.waveInClose(handle)
            api.CloseHandle(event)

    def _drain(self, handle: Any, headers: Any, index: int, *, requeue: bool) -> int:
        hdr_size = ctypes.sizeof(api.WAVEHDR)
        for _ in range(self.BUFFERS):
            header = headers[index]
            if not header.dwFlags & WHDR_DONE:
                break
            if header.dwBytesRecorded:
                data = ctypes.string_at(header.lpData, header.dwBytesRecorded)
                try:
                    self._on_audio(data)
                except Exception:
                    log.exception("audio consumer failed")
            header.dwFlags &= ~WHDR_DONE
            header.dwBytesRecorded = 0
            if requeue:
                api.waveInAddBuffer(handle, ctypes.byref(header), hdr_size)
            index = (index + 1) % self.BUFFERS
        return index
