"""The dormant listener: tray icon + keyboard hook + microphone, no models.

Idle cost: one Python process blocked in GetMessage (0% CPU, ~20 MB), no
microphone access, no network. On the hotkey it starts recording at once and
streams audio to the engine process, which it launches on demand and shuts
down again after the configured warm period.
"""

from __future__ import annotations

import copy
import ctypes
import logging
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from keynone.assets import get_asset
from keynone.cleanup.llm import LLAMA_RUNTIME_ID
from keynone.config import Config, ConfigError, config_mtime, load_config
from keynone.ipc import Msg
from keynone.listener import control
from keynone.listener.engine_proc import EngineProcess
from keynone.listener.hotkeys import Action, HotkeyMachine
from keynone.logging_setup import setup_logging
from keynone.paths import Paths, get_paths
from keynone.sounds import Cues
from keynone.win32 import api, clipboard
from keynone.win32.audio import AudioCapture, AudioError
from keynone.win32.gui import BRAND_RGB, MenuItem, TrayIcon, run_message_loop, write_icon
from keynone.win32.hooks import KeyboardHook
from keynone.win32.keys import Chord, send_menu_mask
from keynone.win32.process import KillOnCloseJob

log = logging.getLogger(__name__)

RELEASE_TAIL_SECONDS = 0.2  # keep recording briefly: people release before the last word ends
CONFIG_POLL_SECONDS = 1.0  # how often an idle listener looks at config.toml's timestamp
FINISH_GRACE_SECONDS = 60.0  # a released dictation is surely finished (or stuck) after this
_REC_COLOR = (229, 72, 77)

# Settings only the listener uses: changing them never restarts a loaded engine.
_LISTENER_ONLY = {
    "hotkeys": ("dictate", "cancel", "paste_last", "hold_threshold_ms", "double_tap_ms"),
    "lifecycle": ("keep_warm_seconds",),
    "audio": ("cues", "device", "max_session_seconds"),
}


def engine_settings_changed(old: Config, new: Config) -> bool:
    """Whether a running engine must restart to use ``new`` (it reads the config once)."""

    def engine_part(config: Config) -> Config:
        part = copy.deepcopy(config)
        for section, names in _LISTENER_ONLY.items():
            for name in names:
                setattr(getattr(part, section), name, None)
        return part

    return engine_part(old) != engine_part(new)


def launch_settings_window(paths: Paths) -> None:
    """Open the settings window in its own process (it shows itself only once)."""
    exe = Path(sys.executable)
    windowed = exe.with_name("pythonw.exe")
    subprocess.Popen([str(windowed if windowed.exists() else exe), "-m", "keynone", "settings"],
                     cwd=str(paths.home), env={**os.environ, "KEYNONE_HOME": str(paths.home)},
                     creationflags=subprocess.CREATE_NO_WINDOW)


def missing_assets(config: Config, paths: Paths) -> list[str]:
    needed = [config.asr.model]
    if config.cleanup.llm_mode != "off":
        needed += [config.llm.model, LLAMA_RUNTIME_ID]
    missing = []
    for asset_id in needed:
        try:
            if not get_asset(asset_id).is_installed(paths):
                missing.append(asset_id)
        except KeyError:
            missing.append(asset_id)
    return missing


class ListenerApp:
    def __init__(self, paths: Paths, *, dry_run: bool = False) -> None:
        self.paths = paths
        self.dry_run = dry_run  # show + transcribe, but never type into apps
        self.config = load_config(paths)
        self._config_mtime = config_mtime(paths)
        self.machine = self._build_machine(self.config)
        self.cues = Cues(paths.cache / "sounds", self.config.audio.cues)
        self.job = KillOnCloseJob()
        self.engine = EngineProcess(self.job, paths, self._on_engine_event)
        self._work: queue.Queue[Any] = queue.Queue()
        self._capture: AudioCapture | None = None
        self._sid = 0
        self._active_sid = 0
        self._session_started = 0.0
        self._session_open = False  # recording, or released and still being finished
        self._session_stopped = 0.0
        self._quit_timer: threading.Timer | None = None
        self._running = True
        self._main_thread = 0
        self._user_paused = False  # "Pause hotkey" in the tray menu
        self._suspended_until = 0.0  # the settings window is recording a new hotkey
        self._next_config_check = 0.0
        icons = paths.cache / "icons"
        self._icon_idle = write_icon(icons / "idle.ico", BRAND_RGB)
        self._icon_rec = write_icon(icons / "recording.ico", _REC_COLOR)
        self.tray = TrayIcon(self._tooltip(), self._icon_idle, self._menu, on_close=self.quit,
                             handlers={control.WM_RELOAD_CONFIG: self._on_reload_request,
                                       control.WM_SUSPEND_HOTKEYS: self._on_suspend_request})

    # ----------------------------------------------------------------- setup

    @staticmethod
    def _build_machine(config: Config) -> HotkeyMachine:
        hk = config.hotkeys
        return HotkeyMachine(Chord.parse(hk.dictate),
                             Chord.parse(hk.cancel) if hk.cancel else None,
                             hold_ms=hk.hold_threshold_ms, double_tap_ms=hk.double_tap_ms,
                             edit=Chord.parse(hk.edit) if hk.edit else None,
                             paste_last=Chord.parse(hk.paste_last) if hk.paste_last else None)

    def _tooltip(self) -> str:
        return f"Keynone - hold {Chord.parse(self.config.hotkeys.dictate)} to dictate"

    def _idle(self) -> bool:
        """No dictation is being recorded or finished, so settings can change safely."""
        finishing = (self._session_open
                     and time.monotonic() - self._session_stopped < FINISH_GRACE_SECONDS)
        return self._capture is None and not self.machine.active and not finishing

    def _maybe_reload_config(self) -> None:
        """Apply config.toml if it changed (call only when idle)."""
        mtime = config_mtime(self.paths)
        if mtime == self._config_mtime:
            return
        self._config_mtime = mtime
        try:
            config = load_config(self.paths)
            machine = self._build_machine(config)
        except (ConfigError, ValueError) as exc:
            self.tray.balloon("Keynone settings error", str(exc)[:250], warning=True)
            return
        old, self.config, self.machine = self.config, config, machine
        self._apply_enabled()
        self.cues.enabled = config.audio.cues
        self.tray.set_tooltip(self._tooltip())
        if engine_settings_changed(old, config):
            self.engine.quit()  # the next dictation starts an engine with the new settings
        log.info("configuration reloaded")

    def _apply_enabled(self) -> None:
        self.machine.enabled = not self._user_paused and not self._suspended_until

    # ----------------------------------------------------------------- messages (main thread)

    def _on_reload_request(self, wparam: int, lparam: int) -> None:
        self._next_config_check = 0.0
        self._work.put(("wake",))

    def _on_suspend_request(self, wparam: int, lparam: int) -> None:
        seconds = min(int(wparam), control.MAX_SUSPEND_SECONDS)
        if seconds and not self._idle():
            return  # never in the middle of a dictation: its release would be lost
        self._suspended_until = time.monotonic() + seconds if seconds else 0.0
        self._apply_enabled()

    # ----------------------------------------------------------------- run

    def run(self) -> int:
        mutex = api.CreateMutexW(None, False, "Local\\Keynone.Listener")
        if ctypes.get_last_error() == api.ERROR_ALREADY_EXISTS:
            log.info("another listener is already running")
            api.CloseHandle(mutex)
            return 1
        self._main_thread = api.GetCurrentThreadId()
        self.tray.show()
        hook = KeyboardHook(self._on_key)
        hook.install()
        worker = threading.Thread(target=self._worker, name="listener", daemon=True)
        worker.start()
        missing = missing_assets(self.config, self.paths)
        if missing:
            self.tray.balloon("Keynone needs its models",
                              f"Missing: {', '.join(missing)}. Run: keynone setup", warning=True)
        else:
            self.tray.balloon("Keynone is ready",
                              f"Hold {Chord.parse(self.config.hotkeys.dictate)} and speak. "
                              "Double-tap for hands-free.")
        log.info("listener running (hotkey %s)", self.config.hotkeys.dictate)
        try:
            run_message_loop()
        finally:
            log.info("listener shutting down")
            self._running = False
            hook.uninstall()
            self._stop_capture()
            self.tray.remove()
            self.engine.quit()
            self.job.close()
            api.CloseHandle(mutex)
            log.info("listener stopped")
        return 0

    def quit(self) -> None:
        self._running = False
        api.PostThreadMessageW(self._main_thread, api.WM_QUIT, 0, 0)

    # ----------------------------------------------------------------- hook (main thread)

    def _on_key(self, vk: int, down: bool) -> bool:
        swallow, actions = self.machine.on_key(vk, down, time.monotonic())
        for action in actions:
            if action is Action.MASK:
                send_menu_mask()  # must happen while Win/Alt is still held
            else:
                self._work.put(action)
        return swallow

    # ----------------------------------------------------------------- worker

    def _worker(self) -> None:
        while self._running:
            deadline = self.machine.next_deadline()
            timeout = 0.25 if deadline is None else max(0.005, deadline - time.monotonic())
            try:
                item = self._work.get(timeout=timeout)
            except queue.Empty:
                item = None
            try:
                for action in self.machine.on_tick(time.monotonic()):
                    self._handle(action)
                if item is not None:
                    self._handle(item)
                if self._capture is not None and (
                    time.monotonic() - self._session_started > self.config.audio.max_session_seconds
                ):
                    log.info("maximum dictation length reached")
                    self._handle(Action.STOP)
                    self.machine.reset()
                self._housekeeping()
            except Exception:
                log.exception("listener action failed")

    def _housekeeping(self) -> None:
        now = time.monotonic()
        if self._suspended_until and now >= self._suspended_until:
            self._suspended_until = 0.0  # the settings window didn't resume us: do it anyway
            self._apply_enabled()
        if now >= self._next_config_check:
            self._next_config_check = now + CONFIG_POLL_SECONDS
            if self._idle():  # never mid-dictation: a new hotkey would lose the release
                self._maybe_reload_config()

    def _handle(self, item: Any) -> None:
        if item is Action.START:
            self._start_session()
        elif item is Action.START_EDIT:
            self._start_session(mode="edit")
        elif item is Action.TO_EDIT:
            if self._capture is not None:
                self.engine.send_json(Msg.MODE, {"mode": "edit"})
                self.cues.play("edit")
        elif item is Action.STOP:
            self._stop_session()
        elif item is Action.CANCEL:
            self._cancel_session()
        elif item is Action.HANDSFREE:
            self.engine.send_json(Msg.MODE, {"mode": "handsfree"})
        elif item is Action.PASTE_LAST:
            self._paste_last()
        elif isinstance(item, tuple):
            kind, *args = item
            if kind == "auto_stop" and args and args[0] == self._active_sid:
                self._stop_session()
                self.machine.reset()
            elif kind == "session_done":
                self._after_session(args[0])
            elif kind == "engine_exited":
                self._engine_exited(args[0])
            elif kind == "balloon":
                self.tray.balloon(args[0], args[1], warning=True)
            # "wake": only interrupts the wait, so housekeeping runs now

    def _start_session(self, mode: str = "ptt") -> None:
        if self._quit_timer is not None:
            self._quit_timer.cancel()
            self._quit_timer = None
        target = int(api.GetForegroundWindow() or 0)
        self.engine.ensure_started()
        self._sid += 1
        self._active_sid = self._sid
        self.engine.send_json(Msg.START, {"sid": self._sid, "mode": mode, "target_hwnd": target,
                                          "insert": not self.dry_run})
        engine = self.engine
        self._capture = AudioCapture(lambda pcm: engine.send(Msg.AUDIO, pcm),
                                     device=self.config.audio.device)
        try:
            self._capture.start()
        except AudioError as exc:
            log.error("microphone unavailable: %s", exc)
            self._capture = None
            self.engine.send(Msg.CANCEL)
            self.machine.reset()
            self.cues.play("error")
            self.tray.balloon("Microphone unavailable", str(exc), warning=True)
            return
        self._session_started = time.monotonic()
        self._session_open = True
        self.cues.play("edit" if mode == "edit" else "start")
        self.tray.set_icon(self._icon_rec)

    def _stop_capture(self) -> None:
        capture, self._capture = self._capture, None
        if capture is not None:
            capture.stop()
            self._session_stopped = time.monotonic()
            self.tray.set_icon(self._icon_idle)

    def _stop_session(self) -> None:
        if self._capture is None:
            return
        time.sleep(RELEASE_TAIL_SECONDS)
        self._stop_capture()
        self.engine.send(Msg.STOP)
        self.cues.play("stop")

    def _cancel_session(self) -> None:
        if self._capture is None:
            return
        self._stop_capture()
        self.engine.send(Msg.CANCEL)
        self.cues.play("cancel")
        self._after_session(self._active_sid)

    def _after_session(self, sid: int) -> None:
        if sid != self._active_sid or self._capture is not None:
            return  # a newer dictation is already running
        self._session_open = False
        self._next_config_check = 0.0  # settings saved meanwhile apply now
        keep = self.config.lifecycle.keep_warm_seconds
        if keep <= 0:
            threading.Thread(target=self.engine.quit, daemon=True).start()
        else:
            if self._quit_timer is not None:
                self._quit_timer.cancel()
            self._quit_timer = threading.Timer(keep, self._quit_if_idle, args=(sid,))
            self._quit_timer.daemon = True
            self._quit_timer.start()

    def _quit_if_idle(self, sid: int) -> None:
        if sid == self._active_sid and self._capture is None:
            log.info("engine idle for the warm period; unloading models")
            self.engine.quit()

    def _engine_exited(self, code: int) -> None:
        self._session_open = False
        if self._capture is not None:  # crashed mid-dictation
            self._stop_capture()
            self.machine.reset()
            self.cues.play("error")
        if code not in (0, None):
            self.tray.balloon("Keynone engine stopped",
                              f"Exit code {code}. See data/logs/engine.log", warning=True)

    # ----------------------------------------------------------------- engine events (reader)

    def _on_engine_event(self, event: dict[str, Any]) -> None:
        name = event.get("event")
        if name == "session_done":
            self._work.put(("session_done", event.get("sid", 0)))
        elif name == "auto_stop":
            self._work.put(("auto_stop", event.get("sid", 0)))
        elif name == "exited":
            self._work.put(("engine_exited", event.get("code")))
        elif name == "warning":
            self._work.put(("balloon", "Keynone", str(event.get("message", ""))))
        log.debug("engine event: %s", event)

    # ----------------------------------------------------------------- tray menu (main thread)

    def _menu(self) -> list[MenuItem]:
        chord = Chord.parse(self.config.hotkeys.dictate)
        paused = self._user_paused
        items: list[MenuItem] = [
            (f"Keynone - hold {chord} to dictate", None),
            (f"Double-tap {chord} for hands-free, Esc cancels", None),
        ]
        if self.config.hotkeys.edit:
            items.append((f"Select text + hold {Chord.parse(self.config.hotkeys.edit)} to edit "
                           "it by voice", None))
        if self.config.hotkeys.paste_last:
            items.append((f"{Chord.parse(self.config.hotkeys.paste_last)} pastes the last "
                          "dictation again", None))
        return [
            *items,
            ("-", None),
            ("Copy last dictation", self._copy_last),
            ("Settings...", lambda: launch_settings_window(self.paths)),
            ("Edit config.toml", lambda: os.startfile(self.paths.config_file)),
            ("Open data folder", lambda: os.startfile(self.paths.data)),
            ("-", None),
            ("Resume hotkey" if paused else "Pause hotkey", self._toggle_pause),
            ("Quit Keynone", self.quit),
        ]

    def _toggle_pause(self) -> None:
        self._user_paused = not self._user_paused
        self._apply_enabled()

    def _last_dictation(self) -> str | None:
        """The text of the last dictation (from history.db), or None after telling why."""
        if not self.config.history.enabled:
            self.tray.balloon("Keynone", "The history is off, so there is no last dictation. "
                                         "Turn it on in Settings > Display & privacy.")
            return None
        from keynone.engine.history import History  # lazy: keeps idle memory small

        history = History(self.paths.history_db, enabled=True, retention_days=0)
        try:
            last = history.last()
        finally:
            history.close()
        if last is None or not last.final_text.strip():
            self.tray.balloon("Keynone", "No dictations yet.")
            return None
        return last.final_text.strip()

    def _copy_last(self) -> None:
        text = self._last_dictation()
        if text is None:
            return
        clipboard.set_text(text, private=False)
        self.tray.balloon("Keynone", "Last dictation copied - press Ctrl+V to paste.")

    def _paste_last(self) -> None:
        """The paste-last hotkey: the last dictation again, into the app in front."""
        if self._capture is not None:
            return  # dictating right now
        text = self._last_dictation()
        if text is None:
            self.cues.play("error")
            return
        from keynone.engine.inject import Inserter  # lazy: keeps idle memory small
        from keynone.win32.windows import window_info

        target = window_info()
        profile = self.config.profile_for(target.exe, target.title)
        trailing = (profile.trailing_space if profile.trailing_space is not None
                    else self.config.insert.trailing_space)
        if trailing and not text.endswith("\n"):
            text += " "
        if self.dry_run:
            log.info("dry run: would paste the last dictation (%d characters)", len(text))
            return
        result = Inserter(self.config.insert).insert(text, profile, target)
        if not result.inserted:
            self.tray.balloon("Keynone", result.message, warning=True)


def stop_running_listener(timeout: float = 5.0) -> bool:
    """Ask a running listener (found by its tray window) to exit cleanly."""
    return control.stop_listener(timeout)


def run_listener(*, verbose: bool = False, dry_run: bool = False) -> int:
    paths = get_paths()
    paths.ensure()
    setup_logging("listener", verbose=verbose, paths=paths)
    api.enable_dpi_awareness()
    return ListenerApp(paths, dry_run=dry_run).run()
