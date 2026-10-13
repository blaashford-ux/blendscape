# SPDX-License-Identifier: GPL-3.0-or-later
"""Command line interface: analyze | plan | validate | render | mix."""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from . import __version__
from .analysis import AnalysisError, TrackAnalysis, analyze
from .audio import AudioError, write_audio
from .model import MixPlan, PlanError, validate
from .planner import MAX_STRETCH, make_plan, tempo_gap
from .render import render
from .stretch import StretchError, available, backend
from .timeline import build_timeline


def _clock(sec: float) -> str:
    return f"{int(sec // 60)}:{sec % 60:04.1f}"


def _say(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _analyze_all(paths: list[str], args) -> list[TrackAnalysis]:
    out = []
    for p in paths:
        _say(f"analysing {p}")
        out.append(analyze(p, cache_dir=args.cache_dir, use_cache=not args.no_cache))
    return out


def _print_plan_summary(plan: MixPlan, explain: bool = False) -> None:
    tl = build_timeline(plan)
    print(f"{len(plan.tracks)} tracks, {len(plan.transitions)} transitions, {plan.sample_rate} Hz, "
          f"{plan.bit_depth}-bit, ceiling {plan.ceiling_db} dBFS")
    for i, t in enumerate(plan.tracks):
        warn = "  (low downbeat confidence: check the bar lines)" if t.confidence and t.confidence < 1.15 else ""
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
            print(f"{r.path}: {r.bpm:.2f} BPM, grid offset {r.offset_s * 1000:.1f} ms, downbeat beat {r.downbeat} "
                  f"(confidence {r.downbeat_confidence:.2f}), {_clock(r.duration_s)}")
            if r.sections:
                print("    sections (bars): " + ", ".join(
                    f"{s['label']} {s['start_bar']}-{s['end_bar']}" for s in r.sections))
    return 0


def _build_plan(args) -> MixPlan:
    analyses = _analyze_all(args.files, args)
    return make_plan(analyses, overlap_beats=args.overlap_beats, style=args.style, phrase_bars=args.phrase_bars,
                     sample_rate=args.sample_rate, bit_depth=args.bit_depth, ceiling_db=args.ceiling_db,
                     mix_points=args.mix_points)


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
    _say(f"stretch backend: {backend()}")
    mix, report = render(plan, progress=_say)
    write_audio(output, mix, plan.sample_rate, plan.bit_depth)
    _say(f"wrote {output}: {_clock(report.duration_s)}, peak before limiting {report.peak_before:.3f}"
         + (f", reduced by {-report.gain_db:.1f} dB" if report.gain_db < -0.05 else ""))
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
    p.add_argument("--ceiling-db", type=float, default=-1.0, help="peak ceiling in dBFS (default -1)")
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
    p.add_argument("--ceiling-db", type=float, help="override the plan's peak ceiling")
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
