"""Optional: start the listener at Windows sign-in (user-initiated only).

This is the one Keynone feature that writes outside the project folder: a
shortcut in the user's Startup folder. It is never done automatically.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def startup_shortcut() -> Path:
    appdata = Path(os.environ["APPDATA"])
    return appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / (
        "Keynone.lnk"
    )


def _pythonw() -> Path:
    exe = Path(sys.executable)
    candidate = exe.with_name("pythonw.exe")
    return candidate if candidate.exists() else exe


def enable() -> Path:
    shortcut = startup_shortcut()
    script = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:KN_LNK); "
        "$s.TargetPath = $env:KN_TARGET; $s.Arguments = '-m keynone listen'; "
        "$s.WorkingDirectory = $env:KN_DIR; $s.Description = 'Keynone voice dictation'; "
        "$s.Save()"
    )
    env = {**os.environ, "KN_LNK": str(shortcut), "KN_TARGET": str(_pythonw()),
           "KN_DIR": str(Path(sys.prefix).parent)}
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                   check=True, env=env, creationflags=subprocess.CREATE_NO_WINDOW)
    return shortcut


def disable() -> bool:
    shortcut = startup_shortcut()
    if shortcut.exists():
        shortcut.unlink()
        return True
    return False


def is_enabled() -> bool:
    return startup_shortcut().exists()
