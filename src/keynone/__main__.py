"""Keynone command line.

    keynone setup              download + verify the default models (once)
    keynone listen             start the dormant hotkey listener (tray icon)
    keynone transcribe a.wav   run a WAV through the full pipeline (dry run)
    keynone doctor             check the installation
    keynone history            show recent dictations
    keynone bench cleanup|asr  quality / speed benchmarks
"""

from __future__ import annotations

import argparse
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
    return run_listener(verbose=args.verbose)


def _cmd_engine(args: argparse.Namespace) -> int:
    from keynone.engine.main import run_engine

    return run_engine(verbose=args.verbose)


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
    api.enable_dpi_awareness()
    job = KillOnCloseJob()
    models = EngineModels(config, paths, job)
    models.start()
    overlay = Overlay(enabled=args.overlay)
    overlay.start()
    status = 0
    # The first file includes the cold start (model loading); later files show
    # the warm engine, which is what consecutive dictations experience.
    for number, wav in enumerate(args.wav, start=1):
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
            "first_live_text_ms": result.first_text_ms,
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
    for asset_id in (config.asr.model, config.llm.model, LLAMA_RUNTIME_ID):
        check(f"asset {asset_id}", get_asset(asset_id).is_installed(paths))
    devices = input_devices()
    check("microphones", bool(devices), ", ".join(devices) or "none found")
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
            polisher = build_polisher(args.model, paths, threads=args.threads, job=job)
            if not polisher.server.wait_ready(60):
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

    from keynone.asr import BYTES_PER_SECOND, Recognizer, model_files
    from keynone.bench import read_wav_mono16k
    from keynone.config import load_config

    config = load_config(paths)
    model = args.model or config.asr.model
    wav = Path(args.wav) if args.wav else (
        paths.models / "nemotron-en-560ms-int8" / "test_wavs" / "1.wav")
    if not wav.exists():
        wav = next((paths.models).rglob("test_wavs/*.wav"))
    pcm = read_wav_mono16k(wav)
    recognizer = Recognizer(model_files(model, paths), threads=args.threads,
                            endpoint_silence=config.asr.endpoint_silence_seconds)
    stream = recognizer.stream()
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
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("setup", help="download and verify models")
    p.add_argument("--with", dest="with_", action="append", metavar="ASSET",
                   help="also install an optional asset (see --list)")
    p.add_argument("--list", action="store_true", help="list available assets")
    p.add_argument("--remove", action="append", metavar="ASSET", help="delete an asset")
    p.set_defaults(func=_cmd_setup)

    sub.add_parser("listen", help="run the hotkey listener").set_defaults(func=_cmd_listen)
    sub.add_parser("engine", help=argparse.SUPPRESS).set_defaults(func=_cmd_engine)

    p = sub.add_parser("transcribe", help="dictate WAV files through the pipeline (no typing)")
    p.add_argument("wav", nargs="+", help="16 kHz mono 16-bit WAV file(s)")
    p.add_argument("--mode", choices=["auto", "always", "off"], help="override cleanup.llm_mode")
    p.add_argument("--realtime", action="store_true", help="feed audio at real-time speed")
    p.add_argument("--overlay", action="store_true", help="show the live overlay")
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
    p.add_argument("--quiet", action="store_true")
    p.set_defaults(func=_cmd_bench)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "bench" and args.what == "cleanup" and args.model is None:
        from keynone.config import load_config

        args.model = load_config().llm.model
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
