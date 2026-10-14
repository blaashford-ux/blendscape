# SPDX-License-Identifier: GPL-3.0-or-later
"""Command line interface: analyze | plan | validate | render | mix."""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from . import __version__
from .analysis import AnalysisError, TrackAnalysis, analyze, with_downbeat
from .audio import AudioError, load_audio, write_audio
from .check import click_check, explain_fix
from .model import MixPlan, PlanError, validate
from .planner import MAX_STRETCH, make_plan, tempo_gap
from .render import render
from .stretch import StretchError, available, backend
from .timeline import build_timeline

LOW_CONFIDENCE = 0.5  # below this the bar-line choice is close to a guess (~half were wrong in testing); check by ear


def _clock(sec: float) -> str:
    return f"{int(sec // 60)}:{sec % 60:04.1f}"


def _say(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _parse_downbeats(items: list[str] | None) -> dict[str, int]:
    out: dict[str, int] = {}
    for item in items or []:
        name, sep, val = item.rpartition("=")
        if not sep or not name or val not in ("0", "1", "2", "3"):
            raise AnalysisError(f"--downbeat expects NAME=N with N in 0..3 (got '{item}')")
        out[name] = int(val)
    return out


def _analyze_all(paths: list[str], args) -> list[TrackAnalysis]:
    overrides = _parse_downbeats(getattr(args, "downbeat", None))
    out = []
    for p in paths:
        _say(f"analysing {p}")
        out.append(analyze(p, cache_dir=args.cache_dir, use_cache=not args.no_cache))
    for name, db in overrides.items():
        hits = [i for i, p in enumerate(paths) if name in (p, Path(p).name, Path(p).stem)]
        if not hits:
            raise AnalysisError(f"--downbeat {name}={db}: no input file matches '{name}'")
        if len({paths[i] for i in hits}) > 1:
            raise AnalysisError(f"--downbeat {name}={db}: '{name}' matches more than one file; give the full path")
        for i in hits:
            out[i] = with_downbeat(out[i], db)
            _say(f"{paths[i]}: downbeat set to beat {db} by hand")
    return out


def _print_plan_summary(plan: MixPlan, explain: bool = False) -> None:
    tl = build_timeline(plan)
    print(f"{len(plan.tracks)} tracks, {len(plan.transitions)} transitions, {plan.sample_rate} Hz, "
          f"{plan.bit_depth}-bit, ceiling {plan.ceiling_db} dBFS")
    for i, t in enumerate(plan.tracks):
        warn = ""
        if t.confidence and t.confidence < LOW_CONFIDENCE:
            warn = f"  (downbeat unsure, {t.confidence:.0%}: check with `blendscape click`, fix with --downbeat)"
        print(f"  {i + 1}. {t.id}: {t.bpm:.2f} BPM, first downbeat beat {t.downbeat}, {_clock(t.duration_s)}{warn}")
    for i, tr in enumerate(plan.transitions):
        a, b = plan.tracks[i], plan.tracks[i + 1]
        s, e = tl.overlap_seconds(i)
        gap = tempo_gap(a, b)
        warn = ""
        if gap > MAX_STRETCH:
            warn = f"  WARNING: tempo gap {gap:.0%} is over {MAX_STRETCH:.0%}; expect audible stretching"
        print(f"  {tr.style} {a.id} -> {b.id}: {tr.overlap_beats} beats, mix {_clock(s)}-{_clock(e)}  "
              f"(out beat {tr.out_beat} @ {a.grid.clock(tr.out_beat)}, "
              f"in beat {tr.in_beat} @ {b.grid.clock(tr.in_beat)}, "
              f"{a.bpm:.1f} -> {b.bpm:.1f} BPM){warn}")
        if tr.score is not None:
            print(f"      score {tr.score:.2f}" + (f"  ({tr.note})" if tr.note else ""))
        if explain:
            for alt in tr.alternatives:
                print(f"      alt: out beat {alt['out_beat']} / in beat {alt['in_beat']}  score {alt['score']:.2f}  "
                      f"{alt.get('note', '')}")


def cmd_analyze(args) -> int:
    results = _analyze_all(args.files, args)
    if args.json:
        print(json.dumps([asdict(r) for r in results], indent=2))
    else:
        for r in results:
            note = "set by hand" if r.downbeat_manual else f"{r.downbeat_confidence:.0%} sure"
            print(f"{r.path}: {r.bpm:.2f} BPM, grid offset {r.offset_s * 1000:.1f} ms, downbeat beat {r.downbeat} "
                  f"({note}), {_clock(r.duration_s)}")
            if not r.downbeat_manual and r.downbeat_confidence < LOW_CONFIDENCE:
                print(f"    unsure about the bar line; check it by ear: blendscape click \"{r.path}\" -o check.wav")
            if r.sections:
                print("    sections (bars): " + ", ".join(
                    f"{s['label']} {s['start_bar']}-{s['end_bar']}" for s in r.sections))
    return 0


def _build_plan(args) -> MixPlan:
    analyses = _analyze_all(args.files, args)
    return make_plan(analyses, overlap_beats=args.overlap_beats, style=args.style, phrase_bars=args.phrase_bars,
                     sample_rate=args.sample_rate, bit_depth=args.bit_depth, ceiling_db=args.ceiling_db,
                     mix_points=args.mix_points)


def cmd_click(args) -> int:
    a = _analyze_all([args.file], args)[0]
    if args.downbeat_n is not None:
        a = with_downbeat(a, args.downbeat_n)
    audio, _ = load_audio(args.file)
    mix, start = click_check(a, audio, args.start, args.seconds)
    write_audio(args.output, mix, a.sample_rate, 16)
    print(f"wrote {args.output}: {args.seconds:.0f} s from {_clock(start)}, {a.bpm:.2f} BPM, "
          f"bar line on grid beat {a.downbeat}" + (" (set by hand)" if a.downbeat_manual else
                                                   f" ({a.downbeat_confidence:.0%} sure)"))
    print("\n".join(explain_fix(a.downbeat)))
    return 0


def cmd_plan(args) -> int:
    plan = _build_plan(args)
    plan.save(args.output)
    _say(f"wrote {args.output}")
    _print_plan_summary(plan, args.explain)
    return 0


def cmd_validate(args) -> int:
    plan = MixPlan.load(args.plan)
    errs = validate(plan)
    if errs:
        print("invalid plan:", file=sys.stderr)
        for e in errs:
            print(f"  - {e}", file=sys.stderr)
        return 1
    _print_plan_summary(plan, explain=True)
    missing = [t.path for t in plan.tracks if not Path(t.path).exists()]
    if missing:
        print("warning: missing files: " + ", ".join(missing), file=sys.stderr)
        return 1
    return 0


def _do_render(plan: MixPlan, output: str, args) -> int:
    if getattr(args, "bit_depth", None):
        plan.bit_depth = args.bit_depth
    if getattr(args, "ceiling_db", None) is not None:
        plan.ceiling_db = args.ceiling_db
    if getattr(args, "no_limiter", False):
        plan.limiter.enabled = False
    if getattr(args, "release_ms", None) is not None:
        plan.limiter.release_ms = args.release_ms
    if getattr(args, "sample_peak", False):
        plan.limiter.true_peak = False
    _say(f"stretch backend: {backend()}")
    mix, report = render(plan, progress=_say)
    write_audio(output, mix, plan.sample_rate, plan.bit_depth)
    if report.clipped:
        _say("warning: the mix goes over 0 dBFS and was clipped; enable the limiter or lower the ceiling")
    if plan.limiter.enabled:
        lim = (f"peak {report.peak_before_db:+.1f} -> {report.peak_after_db:+.1f} dB"
               + (f", limiter reduced by up to {report.max_reduction_db:.1f} dB over "
                  f"{report.limited_fraction:.1%} of the mix" if report.max_reduction_db > 0.05 else ", limiter idle"))
    else:
        lim = f"peak {report.peak_before_db:+.1f} dB, limiter off"
    _say(f"wrote {output}: {_clock(report.duration_s)}, {lim}")
    return 0


def cmd_render(args) -> int:
    return _do_render(MixPlan.load(args.plan), args.output, args)


def cmd_mix(args) -> int:
    plan = _build_plan(args)
    if args.save_plan:
        plan.save(args.save_plan)
        _say(f"wrote {args.save_plan}")
    _print_plan_summary(plan, args.explain)
    return _do_render(plan, args.output, args)


def _add_analysis_opts(p: argparse.ArgumentParser) -> None:
    p.add_argument("--cache-dir", help="analysis cache directory (default: ~/.cache/blendscape or $BLENDSCAPE_CACHE)")
    p.add_argument("--no-cache", action="store_true", help="ignore and do not write the analysis cache")


def _add_downbeat_opt(p: argparse.ArgumentParser) -> None:
    p.add_argument("--downbeat", action="append", metavar="FILE=N",
                   help="force which grid beat (0-3) starts a bar for FILE (name, stem or path); repeatable")


def _add_limiter_opts(p: argparse.ArgumentParser) -> None:
    p.add_argument("--no-limiter", action="store_true", help="turn the limiter off (the mix may clip)")
    p.add_argument("--release-ms", type=float, help="limiter release time in ms (default 120)")
    p.add_argument("--sample-peak", action="store_true", help="limit sample peaks only, not inter-sample peaks")


def _add_plan_opts(p: argparse.ArgumentParser) -> None:
    p.add_argument("files", nargs="+", metavar="FILE", help="audio files in mix order")
    p.add_argument("--overlap-beats", type=int, default=32, help="overlap length in beats (default 32 = 8 bars)")
    p.add_argument("--style", choices=("bass_swap", "crossfade"), default="bass_swap")
    p.add_argument("--phrase-bars", type=int, default=8, help="phrase length used for mix points (default 8 bars)")
    p.add_argument("--mix-points", choices=("smart", "latest"), default="smart",
                   help="smart: score mix points using detected song structure (default); "
                        "latest: leave on the last phrase boundary that fits")
    p.add_argument("--explain", action="store_true", help="also list the runner-up mix points")
    p.add_argument("--sample-rate", type=int, default=44100)
    p.add_argument("--bit-depth", type=int, choices=(16, 24, 32), default=24, help="output bit depth (32 = float)")
    p.add_argument("--ceiling-db", type=float, default=-1.0, help="limiter ceiling in dB, true peak (default -1)")
    _add_limiter_opts(p)
    _add_downbeat_opt(p)
    _add_analysis_opts(p)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="blendscape", description="Automatic DJ-style blending of audio files.")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("analyze", help="detect BPM, beat grid and downbeat of each file")
    p.add_argument("files", nargs="+", metavar="FILE")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    _add_analysis_opts(p)
    p.set_defaults(fn=cmd_analyze)

    p = sub.add_parser("click", help="write a short clip with a metronome on the detected bar line, to check by ear")
    p.add_argument("file")
    p.add_argument("-o", "--output", default="check.wav")
    p.add_argument("--start", type=float, help="start time in seconds (default: the first loud section)")
    p.add_argument("--seconds", type=float, default=30.0)
    p.add_argument("--downbeat", dest="downbeat_n", type=int, choices=(0, 1, 2, 3),
                   help="preview this grid beat as the bar line instead of the detected one")
    _add_analysis_opts(p)
    p.set_defaults(fn=cmd_click)

    p = sub.add_parser("plan", help="analyse files and write an editable mix_plan.json")
    _add_plan_opts(p)
    p.add_argument("-o", "--output", default="mix_plan.json")
    p.set_defaults(fn=cmd_plan)

    p = sub.add_parser("validate", help="check a plan and print where each transition lands")
    p.add_argument("plan")
    p.set_defaults(fn=cmd_validate)

    p = sub.add_parser("render", help="render a plan to an audio file")
    p.add_argument("plan")
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--bit-depth", type=int, choices=(16, 24, 32), help="override the plan's bit depth")
    p.add_argument("--ceiling-db", type=float, help="override the plan's limiter ceiling")
    _add_limiter_opts(p)
    p.set_defaults(fn=cmd_render)

    p = sub.add_parser("mix", help="plan and render in one step")
    _add_plan_opts(p)
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--save-plan", metavar="PLAN.json", help="also keep the generated plan")
    p.set_defaults(fn=cmd_mix)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd in ("render", "mix") and not available():
        try:
            backend()
        except StretchError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
    try:
        return args.fn(args)
    except (PlanError, AnalysisError, AudioError, StretchError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
