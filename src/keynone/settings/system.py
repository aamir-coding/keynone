"""What the settings window needs to know about this computer and installation,
and the few things it can do besides editing config.toml (all of them are also
plain commands: the window runs the same command line in a terminal)."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

from keynone import autostart
from keynone.assets import get_asset
from keynone.editions import Edition, installed_edition, language_pack_installed
from keynone.listener import control
from keynone.paths import Paths

log = logging.getLogger(__name__)

# keynone.cleanup.llm.LLAMA_VULKAN_RUNTIME_ID, without importing the LLM client at start-up
GPU_RUNTIME = "llama.cpp-b11398-vulkan-x64"


class System:
    def __init__(self, paths: Paths) -> None:
        self.paths = paths

    # ----------------------------------------------------------------- installation

    def installed(self, asset_id: str) -> bool:
        try:
            return get_asset(asset_id).is_installed(self.paths)
        except KeyError:
            return False

    def size_mb(self, asset_id: str) -> int:
        """Disk space an installed model or runtime takes."""
        try:
            directory = get_asset(asset_id).directory(self.paths)
            return round(sum(f.stat().st_size for f in directory.rglob("*") if f.is_file()) / 2**20)
        except (KeyError, OSError):
            return 0

    def language_pack(self) -> bool:
        return language_pack_installed(self.paths)

    def edition(self) -> Edition:
        return installed_edition(self.paths)

    def gpu_runtime(self) -> bool:
        return self.installed(GPU_RUNTIME)

    def gpus(self) -> list[str]:
        """Graphics cards the GPU runtime can use (slow the first time: call off the UI)."""
        from keynone.cleanup.llm import available_gpu_devices

        return [d.description for d in available_gpu_devices(self.paths)]

    def microphones(self) -> list[str]:
        from keynone.win32.audio import input_devices

        try:
            return input_devices()
        except OSError:
            log.warning("could not list microphones", exc_info=True)
            return []

    # ----------------------------------------------------------------- the listener

    def listener_running(self) -> bool:
        return control.is_running()

    def settings_saved(self) -> bool:
        """Tell a running listener at once (it would notice within a second anyway)."""
        return control.request_reload()

    def suspend_hotkeys(self, seconds: int) -> bool:
        return control.suspend_hotkeys(seconds)

    def resume_hotkeys(self) -> bool:
        return control.resume_hotkeys()

    def start_listener(self) -> None:
        exe = Path(sys.executable)
        windowed = exe.with_name("pythonw.exe")
        subprocess.Popen([str(windowed if windowed.exists() else exe), "-m", "keynone", "listen"],
                         cwd=str(self.paths.home), env=self._env(),
                         creationflags=subprocess.CREATE_NO_WINDOW)

    # ----------------------------------------------------------------- Windows

    def autostart(self) -> bool:
        return autostart.is_enabled()

    def set_autostart(self, enabled: bool) -> None:
        if enabled:
            autostart.enable()
        else:
            autostart.disable()

    def open(self, path: Path) -> None:
        os.startfile(path)  # a local file or folder, in its default app

    def copy_text(self, text: str) -> None:
        """Put ``text`` on the clipboard (it stays there after the window closes)."""
        from keynone.win32 import clipboard

        clipboard.set_text(text, private=False)

    def run_in_terminal(self, *args: str) -> None:
        """Run ``keynone <args>`` in a new console window that stays open at the end."""
        exe = Path(sys.executable)
        console = exe.with_name("python.exe")
        subprocess.Popen([str(console if console.exists() else exe), "-m", "keynone",
                          "--pause-at-end", *args],
                         cwd=str(self.paths.home), env=self._env(),
                         creationflags=subprocess.CREATE_NEW_CONSOLE)

    def _env(self) -> dict[str, str]:
        return {**os.environ, "KEYNONE_HOME": str(self.paths.home)}
