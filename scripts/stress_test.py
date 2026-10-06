"""Adversarial stress test of the dictation cleanup (fillers, repetitions,
self-corrections, voice commands, lists, questions, formatting).

Cases live in src/keynone/evaldata/dictation_stress.jsonl: what a person *says*
(chunks separated by pauses) and the text they expect to get.

    uv run python scripts/stress_test.py text [--llm]        synthetic recognizer outputs
    uv run python scripts/stress_test.py synth [--voices ...] Windows voices -> data/stress/audio
    uv run python scripts/stress_test.py asr                  speech model -> data/stress/asr
    uv run python scripts/stress_test.py audio [--llm]       replay the recorded recognizer output

``text`` feeds four recognizer styles through the same segmenter and pipeline
as a live dictation: punctuated chunks, unpunctuated chunks, one punctuated
run without pauses and one unpunctuated run. ``audio`` replays exactly what the
speech model produced for synthesized speech (several voices and accents), in
the order and with the pauses it produced them. No network is used: the voices
are the ones installed with Windows.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from keynone.cleanup import formatting
from keynone.cleanup.pipeline import CleanupPipeline, CleanupSettings, Polisher
from keynone.cleanup.segmenter import SentenceSegmenter
from keynone.paths import get_paths

ROOT = Path(__file__).resolve().parents[1]
EVALDATA = ROOT / "src" / "keynone" / "evaldata"
# "main" was used while fixing the rules; "holdout" was written separately and
# measures how well the fixes generalize.
CASE_FILES = {"main": ["dictation_stress.jsonl"], "holdout": ["dictation_stress_holdout.jsonl"],
              "all": ["dictation_stress.jsonl", "dictation_stress_holdout.jsonl"]}

# name -> (Windows voice, SSML language, default rate)
VOICES: dict[str, tuple[str, str, str]] = {
    "ravi": ("Microsoft Ravi", "en-IN", "+0%"),
    "heera": ("Microsoft Heera", "en-IN", "+0%"),
    "mark": ("Microsoft Mark", "en-US", "+0%"),
    "susan": ("Microsoft Susan", "en-GB", "+0%"),
    "zira": ("Microsoft Zira", "en-US", "+0%"),
    "george": ("Microsoft George", "en-GB", "+0%"),
    "ravi-fast": ("Microsoft Ravi", "en-IN", "+30%"),
    "mark-fast": ("Microsoft Mark", "en-US", "+30%"),
}
DEFAULT_VOICES = "ravi,heera,mark,susan,ravi-fast"
PAUSE_MS = 900  # > asr.endpoint_silence_seconds: every chunk boundary is a real pause


@dataclass
class Case:
    id: str
    cat: str
    say: list[str]
    expect: str
    alt: list[str] = field(default_factory=list)
    must: list[str] = field(default_factory=list)
    must_not: list[str] = field(default_factory=list)
    llm: bool = False
    text_only: bool = False
    rate: str = ""
    note: str = ""

    @property
    def accepted(self) -> list[str]:
        return [self.expect, *self.alt]


def load_cases(pattern: str = "", which: str = "main") -> list[Case]:
    cases = [Case(**json.loads(line)) for name in CASE_FILES[which]
             for line in (EVALDATA / name).read_text(encoding="utf-8").splitlines()
             if line.strip()]
    if pattern:
        rx = re.compile(pattern)
        cases = [c for c in cases if rx.search(c.id) or rx.search(c.cat)]
    return cases


# --------------------------------------------------------------------------- recognizer styles

_COMMON = frozenset(
    """a an the this that these those my your our their his her its i we you he she they it
    and or but so then now also please thanks thank yes no okay ok well hi hello hey dear
    what why how where when who which whose can could would will should shall may might must
    do does did is are was were be have has had let's it's that's there's i'm i'll i've i'd
    we're we'll you're they're there here first second third number one two three four five
    finally next after before for to of in on at by with from about if as just maybe sure
    sorry wait actually um uh er hmm ah like scratch new delete strike step bullet see call
    send meet set book order turn tell ship grab invite email text make go come read draw add
    milk eggs bread really very good bye knock come all some any every each""".split()
)


def _bare(text: str, *, first: bool) -> str:
    """An unpunctuated recognizer output: no , . ? ! ; and no sentence capitals."""
    words = []
    sentence_start = True
    for i, token in enumerate(text.split()):
        ends = token[-1:] in ".?!"
        word = re.sub(r"(?<!\d)[,.?!;](?!\d)|[,.?!;]$", "", token)
        if not word:
            continue
        lowered = word.lower()
        if sentence_start and not (first and i == 0) and lowered in _COMMON and not (
                lowered == "i" or lowered.startswith(("i'", "i’"))):
            word = word[0].lower() + word[1:]
        words.append(word)
        sentence_start = ends
    return " ".join(words)


def recognizer_styles(case: Case) -> dict[str, list[str]]:
    """Chunk lists as different recognizers would produce them (chunk = pause)."""
    say = case.say
    bare = [_bare(chunk, first=i == 0) for i, chunk in enumerate(say)]
    return {
        "punct": list(say),
        "bare": bare,
        "run": [" ".join(say)],
        "runbare": [_bare(" ".join(say), first=True)],
    }


# --------------------------------------------------------------------------- running

@dataclass
class Outcome:
    text: str
    fragments: list[str]
    llm_calls: int = 0
    rejections: list[str] = field(default_factory=list)


def run_updates(updates: list[tuple[str, bool]], final: str, settings: CleanupSettings,
                polisher: Polisher | None, finish_timeout: float = 30.0) -> Outcome:
    """Exactly what Session._process does with the recognizer's output."""
    segmenter = SentenceSegmenter()
    fragments: list[str] = []
    pipeline = CleanupPipeline(settings, polisher)
    for text, endpoint in updates:
        for sentence in segmenter.update(text, flush=endpoint):
            fragments.append(sentence)
            pipeline.add_sentence(sentence)
    for sentence in segmenter.update(final, flush=True):
        fragments.append(sentence)
        pipeline.add_sentence(sentence)
    result = pipeline.finish(timeout=finish_timeout)
    text = formatting.finalize(result.text, style="default", drop_final_period=False,
                               trailing_space=False)
    return Outcome(text, fragments, result.llm_calls, list(result.rejections))


def chunks_to_updates(chunks: list[str]) -> tuple[list[tuple[str, bool]], str]:
    """Append-only transcript growing chunk by chunk, with a pause after each chunk."""
    updates = []
    text = ""
    for chunk in chunks:
        text = f"{text} {chunk}".strip()
        updates.append((text, True))
    return updates, text


# --------------------------------------------------------------------------- scoring

_UNITS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
    "fifteen sixteen seventeen eighteen nineteen".split())}
_TENS = {w: 10 * i for i, w in enumerate(
    "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()) if w != "_"}
_ORDINAL_WORDS = {
    "first": "1st", "second": "2nd", "third": "3rd", "fourth": "4th", "fifth": "5th",
    "sixth": "6th", "seventh": "7th", "eighth": "8th", "ninth": "9th", "tenth": "10th",
    "eleventh": "11th", "twelfth": "12th", "thirteenth": "13th", "fourteenth": "14th",
}


def _numbers_to_digits(words: list[str]) -> list[str]:
    out: list[str] = []
    i = 0
    while i < len(words):
        word = words[i]
        if word in _TENS:
            value = _TENS[word]
            if i + 1 < len(words) and words[i + 1] in _UNITS and _UNITS[words[i + 1]] < 10:
                value += _UNITS[words[i + 1]]
                i += 1
            out.append(str(value))
        elif word in _UNITS:
            out.append(str(_UNITS[word]))
        elif word in _ORDINAL_WORDS:
            out.append(_ORDINAL_WORDS[word])
        else:
            out.append(word)
        i += 1
    return out


def norm_loose(text: str) -> str:
    text = re.sub(r"[,;]", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r" *\n *", "\n", text).strip(" ")


def norm_words(text: str) -> str:
    """Words only (case/punctuation-insensitive), keeping lines and list markers."""
    lines = []
    for line in text.split("\n"):
        marker = re.match(r"^\s*(\d+\.|-)\s+", line)
        body = line[marker.end():] if marker else line
        words = re.findall(r"[a-z0-9]+(?:['’][a-z]+)?", body.lower().replace("’", "'"))
        words = [w for w in _numbers_to_digits(words) if w not in ("um", "uh")]
        lines.append(((marker.group(1) + " ") if marker else "") + " ".join(words))
    return "\n".join(lines).strip()


LEVEL_FOR_STYLE = {"punct": "exact", "run": "exact", "bare": "loose", "runbare": "words",
                   "audio": "words"}


def matches(case: Case, output: str, level: str) -> bool:
    norm = {"exact": lambda s: s.strip(" "), "loose": norm_loose, "words": norm_words}[level]
    if any(norm(output) == norm(option) for option in case.accepted):
        return True
    if case.must or case.must_not:
        lowered = output.lower()
        return (all(m.lower() in lowered for m in case.must)
                and not any(m.lower() in lowered for m in case.must_not))
    return False


# --------------------------------------------------------------------------- reporting

@dataclass
class Report:
    rows: list[dict[str, Any]] = field(default_factory=list)

    def add(self, case: Case, style: str, outcome: Outcome, ok: bool, raw: str = "") -> None:
        self.rows.append({"id": case.id, "cat": case.cat, "style": style, "ok": ok,
                          "llm_case": case.llm, "output": outcome.text, "expect": case.expect,
                          "fragments": outcome.fragments, "raw": raw,
                          "llm_calls": outcome.llm_calls, "rejections": outcome.rejections})

    def print(self, *, show_failures: bool, llm_used: bool) -> None:
        table: dict[tuple[str, str], list[bool]] = defaultdict(list)
        for row in self.rows:
            if row["llm_case"] and not llm_used:
                continue
            table[(row["cat"], row["style"])].append(row["ok"])
        styles = sorted({s for _, s in table}, key=lambda s: list(LEVEL_FOR_STYLE).index(s)
                        if s in LEVEL_FOR_STYLE else 99)
        cats = sorted({c for c, _ in table})
        print(f"\n{'category':12s}" + "".join(f"{s:>16s}" for s in styles))
        total = defaultdict(lambda: [0, 0])
        for cat in cats:
            line = f"{cat:12s}"
            for style in styles:
                results = table.get((cat, style), [])
                total[style][0] += sum(results)
                total[style][1] += len(results)
                line += f"{f'{sum(results)}/{len(results)}':>16s}" if results else f"{'-':>16s}"
            print(line)
        print(f"{'TOTAL':12s}" + "".join(
            f"{f'{total[s][0]}/{total[s][1]}':>16s}" for s in styles))
        if not llm_used:
            skipped = len({r['id'] for r in self.rows if r["llm_case"]})
            print(f"({skipped} cases that need the LLM were skipped)")
        if show_failures:
            print()
            for row in self.rows:
                if row["ok"] or (row["llm_case"] and not llm_used):
                    continue
                print(f"FAIL {row['id']} [{row['style']}]")
                if row["raw"]:
                    print(f"   raw:      {row['raw']!r}")
                print(f"   fragments: {row['fragments']!r}")
                print(f"   got:      {row['output']!r}")
                print(f"   expected: {row['expect']!r}")
                if row["rejections"]:
                    print(f"   LLM: {row['llm_calls']} call(s), rejected: {row['rejections']}")

    def save(self, name: str) -> Path:
        out = get_paths().data / "stress" / f"report-{name}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(self.rows, indent=1, ensure_ascii=False), encoding="utf-8")
        return out


# --------------------------------------------------------------------------- LLM

def start_polisher(gpu: str) -> Any:
    from keynone.cleanup.llm import build_polisher
    from keynone.config import load_config
    from keynone.win32.process import KillOnCloseJob

    config = load_config()
    job = KillOnCloseJob()
    polisher = build_polisher(config.llm.model, get_paths(), threads=config.llm.threads,
                              context=config.llm.context, gpu=gpu, job=job)
    if not polisher.server.wait_ready(120):
        raise SystemExit("llama-server did not start (see data/logs/llama-server.log)")
    polisher.prime("en")
    polisher._job = job  # keep the job (and the server) alive with the polisher
    return polisher


def settings_for(llm: bool) -> CleanupSettings:
    from keynone.config import load_config

    config = load_config()
    return CleanupSettings(llm_mode="auto" if llm else "off",
                           llm_timeout=config.cleanup.llm_timeout_seconds)


# --------------------------------------------------------------------------- commands

def cmd_text(args: argparse.Namespace) -> int:
    cases = load_cases(args.filter)
    polisher = start_polisher(args.gpu) if args.llm else None
    settings = settings_for(args.llm)
    report = Report()
    styles = args.styles.split(",")
    started = time.perf_counter()
    for case in cases:
        if case.llm and not args.llm:
            for style in styles:
                report.add(case, style, Outcome("", []), False)
            continue
        for style, chunks in recognizer_styles(case).items():
            if style not in styles:
                continue
            updates, final = chunks_to_updates(chunks)
            outcome = run_updates(updates, final, settings, polisher)
            report.add(case, style, outcome, matches(case, outcome.text, LEVEL_FOR_STYLE[style]))
    print(f"{len(cases)} cases in {time.perf_counter() - started:.1f}s")
    report.print(show_failures=not args.quiet, llm_used=args.llm)
    if args.save:
        print(f"saved {report.save(args.save)}")
    if polisher is not None:
        polisher.close()
    return 0


def _ssml(case: Case, lang: str, rate: str) -> str:
    body = '<break time="300ms"/>'
    for chunk in case.say:
        body += f'<prosody rate="{case.rate or rate}">{escape(chunk)}</prosody>'
        body += f'<break time="{PAUSE_MS}ms"/>'
    return (f'<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" '
            f'xml:lang="{lang}">{body}</speak>')


def cmd_synth(args: argparse.Namespace) -> int:
    base = get_paths().data / "stress" / "audio"
    jobs = []
    for voice in args.voices.split(","):
        display, lang, rate = VOICES[voice]
        folder = base / voice
        folder.mkdir(parents=True, exist_ok=True)
        for case in load_cases(args.filter):
            out = folder / f"{case.id}.wav"
            if case.text_only or (out.exists() and not args.force):
                continue
            jobs.append({"voice": display, "ssml": _ssml(case, lang, rate), "out": str(out)})
    if not jobs:
        print("nothing to synthesize")
        return 0
    job_file = base / "jobs.json"
    job_file.write_text(json.dumps(jobs), encoding="utf-8")
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                    str(ROOT / "scripts" / "tts_windows.ps1"), str(job_file)], check=True)
    job_file.unlink()
    return 0


def cmd_asr(args: argparse.Namespace) -> int:
    from keynone.asr import Recognizer, model_files
    from keynone.bench import read_wav_mono16k
    from keynone.config import load_config

    paths = get_paths()
    config = load_config(paths)
    recognizer = Recognizer(model_files(config.asr.model, paths), threads=config.asr.threads,
                            endpoint_silence=config.asr.endpoint_silence_seconds)
    audio_dir = paths.data / "stress" / "audio"
    out_dir = paths.data / "stress" / "asr"
    wanted = load_cases(args.filter)
    ids = {c.id for c in wanted}
    count = 0
    for voice in args.voices.split(","):
        for wav in sorted((audio_dir / voice).glob("*.wav")):
            if wav.stem not in ids:
                continue
            out = out_dir / voice / f"{wav.stem}.json"
            if out.exists() and not args.force:
                continue
            pcm = read_wav_mono16k(wav)
            if args.gain_db:
                import numpy as np

                samples = np.frombuffer(pcm, np.int16).astype(np.float32)
                samples *= 10 ** (args.gain_db / 20)
                pcm = np.clip(samples, -32768, 32767).astype(np.int16).tobytes()
            stream = recognizer.stream("en")
            updates: list[tuple[str, bool]] = []
            for i in range(0, len(pcm), 640):  # 20 ms frames, like the microphone
                update = stream.accept_pcm16(pcm[i:i + 640])
                if update:
                    updates.append(update)
            final = stream.finish()
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps({"updates": updates, "final": final}, ensure_ascii=False),
                           encoding="utf-8")
            count += 1
    print(f"recognized {count} file(s)")
    return 0


def cmd_audio(args: argparse.Namespace) -> int:
    cases = {c.id: c for c in load_cases(args.filter)}
    polisher = start_polisher(args.gpu) if args.llm else None
    settings = settings_for(args.llm)
    report = Report()
    asr_dir = get_paths().data / "stress" / "asr"
    for voice in args.voices.split(","):
        for path in sorted((asr_dir / voice).glob("*.json")):
            case = cases.get(path.stem)
            if case is None:
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            updates = [(text, bool(endpoint)) for text, endpoint in data["updates"]]
            if case.llm and not args.llm:
                report.add(case, f"audio:{voice}", Outcome("", []), False, data["final"])
                continue
            outcome = run_updates(updates, data["final"], settings, polisher)
            report.add(case, f"audio:{voice}", outcome, matches(case, outcome.text, "words"),
                       data["final"])
    report.print(show_failures=not args.quiet, llm_used=args.llm)
    if args.save:
        print(f"saved {report.save(args.save)}")
    if polisher is not None:
        polisher.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name, func in (("text", cmd_text), ("synth", cmd_synth), ("asr", cmd_asr),
                       ("audio", cmd_audio)):
        p = sub.add_parser(name)
        p.set_defaults(func=func)
        p.add_argument("--filter", default="", help="regex on case id or category")
        if name in ("text", "audio"):
            p.add_argument("--llm", action="store_true", help="use the local LLM (auto mode)")
            p.add_argument("--gpu", default="off", help='LLM device: "off" or "auto"')
            p.add_argument("--quiet", action="store_true", help="summary only")
            p.add_argument("--save", default="", help="save the rows as data/stress/report-NAME")
        if name == "text":
            p.add_argument("--styles", default="punct,bare,run,runbare")
        if name in ("synth", "asr", "audio"):
            p.add_argument("--voices", default=DEFAULT_VOICES)
        if name in ("synth", "asr"):
            p.add_argument("--force", action="store_true", help="redo existing files")
        if name == "asr":
            p.add_argument("--gain-db", type=float, default=0.0,
                           help="scale the audio first (e.g. -30 for a distant microphone)")
    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
