# SPDX-License-Identifier: GPL-3.0-or-later
"""A plan described in plain numbers and warnings, for clients (the web UI) rather than the terminal."""
from __future__ import annotations

from pathlib import Path

from .model import MixPlan
from .planner import MAX_STRETCH, tempo_gap, unplayed_s
from .timeline import build_timeline

LOW_CONFIDENCE = 0.5  # below this the bar-line choice is close to a guess


def describe_plan(plan: MixPlan) -> dict:
    """Tracks, transitions (with where they land on the output clock, what is skipped, warnings) and the total
    length. Assumes the plan is valid."""
    tl = build_timeline(plan)
    sr = plan.sample_rate
    tracks = []
    for t in plan.tracks:
        tracks.append({
            "id": t.id, "name": Path(t.path).name, "bpm": t.bpm, "downbeat": t.downbeat, "confidence": t.confidence,
            "duration_s": t.duration_s, "end_s": t.end_s, "sections": t.sections, "offset_s": t.offset_s,
        })
    transitions = []
    for i, tr in enumerate(plan.transitions):
        a, b = plan.tracks[i], plan.tracks[i + 1]
        start_s, end_s = tl.overlap_seconds(i)
        gap = tempo_gap(a, b)
        warnings = []
        if gap > MAX_STRETCH:
            warnings.append(f"Tempo gap {gap:.0%} is over {MAX_STRETCH:.0%}: expect audible stretching.")
        for t in (a, b):
            if t.confidence and t.confidence < LOW_CONFIDENCE:
                name = Path(t.path).name
                warnings.append(f"Bar line of {name} is a guess ({t.confidence:.0%} sure): check it by ear.")
        cut = unplayed_s(a, tr.out_beat, tr.overlap_beats)
        transitions.append({
            "index": i, "from": a.id, "to": b.id, "style": tr.style, "overlap_beats": tr.overlap_beats,
            "overlap_s": tr.overlap_beats * a.grid.period_s, "out_beat": tr.out_beat, "in_beat": tr.in_beat,
            "out_time_s": a.grid.beat_time(tr.out_beat), "in_time_s": b.grid.beat_time(tr.in_beat),
            "start_s": start_s, "end_s": end_s, "unplayed_tail_s": max(0.0, cut),
            "tempo_gap": gap, "score": tr.score, "note": tr.note, "eq": _eq(tr), "warnings": warnings,
            "alternatives": [
                {**alt, "out_time_s": a.grid.beat_time(alt["out_beat"]), "in_time_s": b.grid.beat_time(alt["in_beat"])}
                for alt in tr.alternatives
            ],
        })
    last = tl.spans[-1]
    total = (last.shift + plan.tracks[-1].duration_s * sr) / sr
    return {"tracks": tracks, "transitions": transitions, "total_duration_s": total, "sample_rate": sr}


def _eq(tr) -> dict:
    eq = tr.eq
    return {"bass_swap_center": eq.bass_swap_center, "bass_swap_beats": eq.bass_swap_beats,
            "mid_in": list(eq.mid_in), "mid_out": list(eq.mid_out), "hi_in": list(eq.hi_in),
            "hi_out": list(eq.hi_out)}
