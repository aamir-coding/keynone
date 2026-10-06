"""Keynone command line.

    keynone setup              download + verify the default models (once)
    keynone listen             start the dormant hotkey listener (tray icon)
    keynone settings           open the settings window (edits data/config.toml)
    keynone transcribe a.wav   run a WAV through the full pipeline (dry run)
    keynone doctor             check the installation
    keynone history            show recent dictations
    keynone bench cleanup|asr  quality / speed benchmarks
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
import time
from pathlib import Path


def _cmd_setup(args: argparse.Namespace) -> int:
    from keynone.assets import REGISTRY, get_asset, install, remove
    from keynone.paths import get_paths

    paths = get_paths()
    paths.ensure()
    if args.list:
        for asset in REGISTRY:
            mark = "installed" if asset.is_installed(paths) else ("default" if asset.default
                                                                  else "optional")
            print(f"{asset.id:30s} {mark:10s} {asset.title}\n{'':41s}license: {asset.license}")
        return 0
    if args.remove:
        for asset_id in args.remove:
            remove(get_asset(asset_id), paths)
            print(f"removed {asset_id}")
        return 0
    wanted = [a for a in REGISTRY if a.default] + [get_asset(i) for i in args.with_ or []]

    def progress(name: str, done: int, total: int | None) -> None:
        pct = f"{100 * done / total:5.1f}%" if total else ""
        print(f"\r  {name}: {done / 1e6:7.1f} MB {pct}", end="", flush=True)

    for asset in wanted:
        if asset.is_installed(paths):
            print(f"[ok] {asset.id}")
            continue
        print(f"[..] {asset.id} - {asset.title} ({asset.license})")
        install(asset, paths, progress)
        print(f"\n[ok] {asset.id}")
    print("Setup complete. Start dictating with:  keynone listen")
    return 0


def _cmd_listen(args: argparse.Namespace) -> int:
    from keynone.listener.main import run_listener

    if sys.stdout is not None and sys.stdout.isatty():
        print("Keynone is running in the tray. Hold Ctrl+Win (default) to dictate; "
              "right-click the tray icon to quit.")
        if args.dry_run:
            print("Dry run: dictations are shown and saved to history but not typed.")
    return run_listener(verbose=args.verbose, dry_run=args.dry_run)


def _cmd_settings(args: argparse.Namespace) -> int:
    from keynone.settings.app import run_settings

    return run_settings()


def _cmd_engine(args: argparse.Namespace) -> int:
    from keynone.engine.main import run_engine

    return run_engine(verbose=args.verbose, overlay_enabled=False if args.no_overlay else None)


def _cmd_autostart(args: argparse.Namespace) -> int:
    from keynone import autostart

    if args.action == "enable":
        print(f"Keynone will start at sign-in: {autostart.enable()}")
    elif args.action == "disable":
        print("Autostart removed." if autostart.disable() else "Autostart was not enabled.")
    else:
        print("enabled" if autostart.is_enabled() else "disabled")
    return 0


def _cmd_stop(args: argparse.Namespace) -> int:
    from keynone.listener.main import stop_running_listener

    if stop_running_listener():
        print("Keynone listener stopped.")
        return 0
    print("Keynone listener is not running.")
    return 1


def _cmd_transcribe(args: argparse.Namespace) -> int:
    from keynone.bench import read_wav_mono16k
    from keynone.config import load_config
    from keynone.engine.models import EngineModels
    from keynone.engine.overlay import Overlay
    from keynone.engine.session import Session, SessionMeta, SessionOutcome
    from keynone.logging_setup import setup_logging
    from keynone.paths import get_paths
    from keynone.win32 import api
    from keynone.win32.process import KillOnCloseJob

    paths = get_paths()
    setup_logging("transcribe", verbose=args.verbose, paths=paths)
    config = load_config(paths)
    if args.mode:
        config.cleanup.llm_mode = args.mode
    if args.language:
        config.asr.language = args.language
    if args.translate_to is not None:
        config.cleanup.translate_to = args.translate_to
    if args.gpu:
        config.llm.gpu = args.gpu
    api.enable_dpi_awareness()
    job = KillOnCloseJob()
    models = EngineModels(config, paths, job)
    models.start()
    overlay = Overlay(enabled=args.overlay)
    overlay.start()
    status = 0
    # Windows shells don't expand wildcards: do it here ("data/test_audio/*.wav").
    files = [match for pattern in args.wav
             for match in (sorted(glob.glob(pattern)) if glob.has_magic(pattern) else [pattern])]
    # The first file includes the cold start (model loading); later files show
    # the warm engine, which is what consecutive dictations experience.
    for number, wav in enumerate(files, start=1):
        pcm = read_wav_mono16k(Path(wav))
        outcome: list[SessionOutcome] = []
        session = Session(SessionMeta(sid=number, insert=False), config=config, models=models,
                          overlay=overlay, inserter=None, history=None,
                          on_finished=outcome.append)
        started = time.perf_counter()
        session.start()
        frame = 640  # 20 ms, like the microphone path
        for i in range(0, len(pcm), frame):
            session.feed(pcm[i : i + frame])
            if args.realtime:
                time.sleep(max(0.0, started + (i + frame) / 32000 - time.perf_counter()))
        session.stop()
        session.done.wait(120)
        result = outcome[0] if outcome else None
        if result is None:
            print(f"{wav}: no result")
            status = 1
            continue
        print(json.dumps({
            "file": Path(wav).name,
            "engine": "cold" if number == 1 else "warm",
            "status": result.status,
            "audio_seconds": round(result.audio_seconds, 2),
            # Only meaningful when audio arrives at real-time speed, like a microphone.
            "live_lag_ms": {"p50": result.live_lag_p50_ms, "p95": result.live_lag_p95_ms,
                            "max": result.live_lag_max_ms} if args.realtime else None,
            "stop_to_text_ms": result.latency_ms,
            "llm_calls": result.llm_calls,
        }))
        print(f"  RAW:   {result.raw_text}\n  FINAL: {result.text!r}\n")
    overlay.close()
    models.close()
    job.close()
    return status


def _cmd_history(args: argparse.Namespace) -> int:
    from keynone.engine.history import History
    from keynone.paths import get_paths

    history = History(get_paths().history_db, retention_days=0)
    for item in reversed(history.recent(args.n)):
        stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(item.created_at))
        print(f"{stamp}  {item.app or '?':18s} {item.audio_seconds:5.1f}s  "
              f"{item.latency_ms:5d} ms  {item.final_text.strip()}")
    history.close()
    return 0


def _cmd_devices(args: argparse.Namespace) -> int:
    from keynone.win32.audio import input_devices

    for index, name in enumerate(input_devices()):
        print(f"{index}: {name}")
    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    from keynone.assets import get_asset
    from keynone.cleanup.llm import LLAMA_RUNTIME_ID
    from keynone.config import ConfigError, load_config
    from keynone.paths import get_paths
    from keynone.win32.audio import input_devices
    from keynone.win32.keys import Chord
    from keynone.win32.process import is_elevated

    paths = get_paths()
    ok = True

    def check(label: str, passed: bool, detail: str = "") -> None:
        nonlocal ok
        ok &= passed
        print(f"[{'ok' if passed else '!!'}] {label}{': ' + detail if detail else ''}")

    print(f"home: {paths.home}")
    check("python", sys.version_info >= (3, 12), sys.version.split()[0])
    try:
        config = load_config(paths)
        check("config", True, str(paths.config_file))
        check("hotkey", True, str(Chord.parse(config.hotkeys.dictate)))
    except (ConfigError, ValueError) as exc:
        check("config", False, str(exc))
        return 1
    from keynone.asr import effective_model
    from keynone.cleanup.llm import LLAMA_VULKAN_RUNTIME_ID, list_gpu_devices, resolve_gpu_device
    from keynone.win32.uia import UiaClient

    asr_model = effective_model(config.asr.model, config.asr.language)
    print(f"speech language: {config.asr.language} (model {asr_model})")
    for asset_id in (asr_model, config.llm.model, LLAMA_RUNTIME_ID):
        check(f"asset {asset_id}", get_asset(asset_id).is_installed(paths))
    devices = input_devices()
    check("microphones", bool(devices), ", ".join(devices) or "none found")
    vulkan = get_asset(LLAMA_VULKAN_RUNTIME_ID)
    if vulkan.is_installed(paths):
        from keynone.assets import find_file

        gpus = list_gpu_devices(find_file(vulkan.directory(paths), "llama-server.exe"))
        print("GPUs: " + (", ".join(f"{g.name} {g.description} ({g.memory_mb} MiB)"
                                    for g in gpus) or "none"))
    if config.llm.gpu.lower() not in ("", "off", "none", "cpu"):
        selected = resolve_gpu_device(config.llm.gpu, paths)
        check(f"LLM GPU ({config.llm.gpu})", selected is not None,
              f"{selected[1].description}" if selected else
              f"not available - run: keynone setup --with {LLAMA_VULKAN_RUNTIME_ID}")
    else:
        print("LLM GPU: off (set llm.gpu = \"auto\" to use one)")
    if config.insert.smart_spacing or config.hotkeys.edit:
        uia = UiaClient()
        check("UI Automation (smart spacing, speak-to-edit)", uia.check())
        uia.close()
    check("not running as admin", not is_elevated(),
          "admin windows still need Keynone started as admin" if is_elevated() else "")
    print("all good" if ok else "problems found (see above)")
    return 0 if ok else 1


def _cmd_bench(args: argparse.Namespace) -> int:
    from keynone.paths import get_paths

    paths = get_paths()
    if args.what == "cleanup":
        from keynone.bench import run_cleanup_eval
        from keynone.cleanup.llm import build_polisher
        from keynone.win32.process import KillOnCloseJob

        job = KillOnCloseJob()
        polisher = None
        if args.mode != "off":
            polisher = build_polisher(args.model, paths, threads=args.threads,
                                      gpu=args.gpu or "off", job=job)
            print(f"LLM device: {polisher.server.gpu_device or 'CPU'}")
            if not polisher.server.wait_ready(90):
                print("llama-server failed to start (see data/logs/llama-server.log)")
                return 1
            polisher.prime()
        summary = run_cleanup_eval(polisher, args.mode, verbose=not args.quiet)
        print(json.dumps({k: v for k, v in summary.items() if k != "failures"}, indent=2))
        for failure in summary["failures"]:
            print("FAIL", failure)
        if polisher:
            polisher.close()
        return 0 if not summary["failures"] else 1

    from keynone.asr import (
        BYTES_PER_SECOND,
        MULTILINGUAL_MODELS,
        Recognizer,
        effective_model,
        model_files,
    )
    from keynone.bench import read_wav_mono16k
    from keynone.config import load_config

    config = load_config(paths)
    language = args.language or config.asr.language
    model = args.model or effective_model(config.asr.model, language)
    wav = Path(args.wav) if args.wav else paths.data / "test_audio" / "long.wav"
    if not wav.exists():
        print(f"{wav} not found: pass --wav FILE (16 kHz mono WAV) or run "
              "scripts/make_test_audio.py")
        return 1
    pcm = read_wav_mono16k(wav)
    recognizer = Recognizer(model_files(model, paths), threads=args.threads,
                            endpoint_silence=config.asr.endpoint_silence_seconds,
                            multilingual=model in MULTILINGUAL_MODELS)
    stream = recognizer.stream(language)
    started = time.perf_counter()
    worst = 0.0
    for i in range(0, len(pcm), 640):
        t = time.perf_counter()
        stream.accept_pcm16(pcm[i : i + 640])
        worst = max(worst, time.perf_counter() - t)
    text = stream.finish()
    elapsed = time.perf_counter() - started
    seconds = len(pcm) / BYTES_PER_SECOND
    print(json.dumps({
        "model": model, "threads": args.threads, "audio_seconds": round(seconds, 2),
        "load_seconds": round(recognizer.load_seconds, 2), "rtf": round(elapsed / seconds, 3),
        "worst_chunk_ms": round(worst * 1000),
    }, indent=2))
    print(text)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="keynone", description="Local AI voice dictation.")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    # Used by the settings window, which runs commands in a new terminal window.
    parser.add_argument("--pause-at-end", action="store_true", help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("setup", help="download and verify models")
    p.add_argument("--with", dest="with_", action="append", metavar="ASSET",
                   help="also install an optional asset (see --list)")
    p.add_argument("--list", action="store_true", help="list available assets")
    p.add_argument("--remove", action="append", metavar="ASSET", help="delete an asset")
    p.set_defaults(func=_cmd_setup)

    p = sub.add_parser("listen", help="run the hotkey listener")
    p.add_argument("--dry-run", action="store_true",
                   help="show and transcribe dictations but never type into apps")
    p.set_defaults(func=_cmd_listen)
    sub.add_parser("stop", help="stop a running listener").set_defaults(func=_cmd_stop)
    sub.add_parser("settings", help="open the settings window").set_defaults(func=_cmd_settings)
    p = sub.add_parser("autostart", help="start Keynone at Windows sign-in (Startup shortcut)")
    p.add_argument("action", choices=["enable", "disable", "status"])
    p.set_defaults(func=_cmd_autostart)
    p = sub.add_parser("engine", help=argparse.SUPPRESS)
    p.add_argument("--no-overlay", action="store_true", help=argparse.SUPPRESS)
    p.set_defaults(func=_cmd_engine)

    p = sub.add_parser("transcribe", help="dictate WAV files through the pipeline (no typing)")
    p.add_argument("wav", nargs="+", help="16 kHz mono 16-bit WAV file(s)")
    p.add_argument("--mode", choices=["auto", "always", "off"], help="override cleanup.llm_mode")
    p.add_argument("--realtime", action="store_true", help="feed audio at real-time speed")
    p.add_argument("--overlay", action="store_true", help="show the live overlay")
    p.add_argument("--language", help='override asr.language ("auto", "hi", "es", ...)')
    p.add_argument("--translate-to", dest="translate_to", default=None,
                   help='override cleanup.translate_to (e.g. "English")')
    p.add_argument("--gpu", help='override llm.gpu ("off", "auto", device name)')
    p.set_defaults(func=_cmd_transcribe)

    p = sub.add_parser("history", help="show recent dictations")
    p.add_argument("-n", type=int, default=20)
    p.set_defaults(func=_cmd_history)

    sub.add_parser("devices", help="list microphones").set_defaults(func=_cmd_devices)
    sub.add_parser("doctor", help="check the installation").set_defaults(func=_cmd_doctor)

    p = sub.add_parser("bench", help="benchmarks")
    p.add_argument("what", choices=["cleanup", "asr"])
    p.add_argument("--model", default=None)
    p.add_argument("--mode", default="auto", choices=["auto", "always", "off"])
    p.add_argument("--threads", type=int, default=4)
    p.add_argument("--wav", default=None)
    p.add_argument("--language", default=None, help='asr: "auto", "en", "hi", ...')
    p.add_argument("--gpu", default=None, help='cleanup: "off", "auto" or a device name')
    p.add_argument("--quiet", action="store_true")
    p.set_defaults(func=_cmd_bench)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "bench" and args.what == "cleanup" and args.model is None:
        from keynone.config import load_config

        args.model = load_config().llm.model
    if not args.pause_at_end:
        return int(args.func(args) or 0)
    import ctypes

    ctypes.windll.kernel32.SetConsoleTitleW(f"Keynone - {args.command}")
    try:
        status = int(args.func(args) or 0)
    except Exception as exc:  # show it: this window was opened for the user to read
        print(f"\nFailed: {exc}")
        status = 1
    input("\nPress Enter to close this window.")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
