# SPDX-License-Identifier: GPL-3.0-or-later
"""Build a mix plan from analysed tracks, choosing where each transition happens.

Smart mode scores every phrase-aligned (outgoing bar, incoming bar) pair and keeps the best, with runners-up
recorded in the plan. Hard constraints: both points are on the phrase grid (8 bars by default), the whole overlap fits
inside both tracks, and the outgoing track has finished entering. Soft preferences (weights below):

* out   - the outgoing track leaves from its outro (or into a breakdown), not from the middle of a body
* in    - the incoming track enters through its intro, and its drop lands as the overlap ends
* clash - little bass in both tracks at the same bars
* util  - play most of the outgoing track (leave in its last third), and don't skip the incoming track's start
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .analysis import TrackAnalysis
from .grid import BEATS_PER_BAR
from .model import EQCurves, MixPlan, PlanError, TrackEntry, Transition, check
from .sections import Section, is_drop

WEIGHTS = {"out": 0.25, "in": 0.30, "clash": 0.15, "util_a": 0.25, "util_b": 0.05}
PLAYED_MIN, PLAYED_FULL = 0.35, 0.70  # utilisation: 0 if A leaves before 35% of its length, full from 70%
MAX_STRETCH = 0.08  # tempo change beyond +-8% starts to sound processed
N_ALTERNATIVES = 3


@dataclass
class Profile:
    """What the scorer needs to know about one track."""

    n_bars: int
    sections: list[Section]
    low: np.ndarray  # per-bar low-band level, 0..1 (1 = as loud as the track gets)

    @classmethod
    def from_analysis(cls, a: TrackAnalysis, n_bars: int) -> Profile:
        secs = [Section.from_dict(d) for d in a.sections] or [Section("body", 0, n_bars)]
        low = np.asarray(a.bar_low, dtype=float)
        if low.size and np.percentile(low, 90) > 0:
            low = np.clip(low / np.percentile(low, 90), 0.0, 1.0)
        return cls(n_bars, secs, low)


@dataclass
class Candidate:
    out_beat: int
    in_beat: int
    score: float
    parts: dict[str, float] = field(default_factory=dict)
    note: str = ""

    def to_dict(self) -> dict:
        return {"out_beat": self.out_beat, "in_beat": self.in_beat, "score": round(self.score, 3), "note": self.note}


def _frac_in(sections: list[Section], label: str, bar: int, n_bars: int) -> float:
    spans = [(s.start_bar, s.end_bar) for s in sections if s.label == label]
    hit = sum(1 for b in range(bar, bar + n_bars) if any(lo <= b < hi for lo, hi in spans))
    return hit / max(1, n_bars)


def _starting_at(sections: list[Section], bar: int) -> tuple[int, Section] | None:
    for i, s in enumerate(sections):
        if s.start_bar == bar:
            return i, s
    return None


def score_out(sections: list[Section], bar: int, n_bars: int) -> tuple[float, str]:
    """How good a place is ``bar`` for the outgoing track to start leaving."""
    start = _starting_at(sections, bar)
    outro, brk = _frac_in(sections, "outro", bar, n_bars), _frac_in(sections, "breakdown", bar, n_bars)
    if start and start[1].label == "outro":
        s, note = 1.0, "leaves at the start of its outro"
    elif outro >= 0.99:
        s, note = 0.95, "leaves inside its outro"
    else:
        s = 0.3 + 0.6 * outro
        note = "leaves partly through its outro" if outro else "leaves from the middle of a body"
    if start and start[1].label == "breakdown" and s < 0.75:
        s, note = 0.75, "leaves as its breakdown starts"
    elif brk >= 0.5 and s < 0.6:
        s, note = 0.6, "leaves through a breakdown"
    return s, note


def score_in(sections: list[Section], bar: int, n_bars: int) -> tuple[float, str]:
    """How good a place is ``bar`` for the incoming track to enter."""
    start = _starting_at(sections, bar)
    intro = _frac_in(sections, "intro", bar, n_bars)
    end = _starting_at(sections, bar + n_bars)
    drop_at_end = bool(end) and is_drop(sections, end[0])
    if start and start[1].label == "intro":
        s, note = 0.8, "enters at the start of its intro"
    elif intro >= 0.99:
        s, note = 0.7, "enters inside its intro"
    elif intro > 0:
        s, note = 0.3 + 0.4 * intro, "enters partly through its intro"
    elif start and is_drop(sections, start[0]):
        s, note = 0.5, "enters on its drop"
    elif start:
        s, note = 0.35, "enters at a section start"
    else:
        s, note = 0.1, "enters mid-section"
    if drop_at_end:
        s, note = max(0.85, min(1.0, s + 0.2)), note + "; its drop lands as the overlap ends"
    return s, note


def score_clash(a: Profile, b: Profile, out_bar: int, in_bar: int, n_bars: int) -> float:
    """1 = never both heavy in the bass at once, 0 = always."""
    both = []
    for j in range(n_bars):
        ia, ib = out_bar + j, in_bar + j
        if ia < len(a.low) and ib < len(b.low):
            both.append(min(a.low[ia], b.low[ib]))
    return 1.0 - float(np.mean(both)) if both else 1.0


def _combine(parts: dict[str, float]) -> float:
    return sum(WEIGHTS[k] * parts[k] for k in WEIGHTS) / sum(WEIGHTS.values())


def candidates(a: TrackEntry, b: TrackEntry, pa: Profile, pb: Profile, overlap_beats: int, free_from: int,
               phrase_bars: int, last_track: bool) -> list[Candidate]:
    """All phrase-aligned (out, in) pairs that satisfy the hard constraints, best first."""
    nb_bars = math.ceil(overlap_beats / BEATS_PER_BAR)
    step = phrase_bars * BEATS_PER_BAR
    last_a = a.n_beats - 1 - overlap_beats
    last_b = b.n_beats - 1 - overlap_beats
    outs = [k for k in range(a.downbeat, a.n_beats, step) if free_from <= k <= last_a]
    # an incoming point must leave room for B's own next overlap unless B is the last track
    ins = [k for k in range(b.downbeat, b.n_beats, step) if k <= (last_b if last_track else last_b - overlap_beats)]
    out: list[Candidate] = []
    for ko in outs:
        oa = (ko - a.downbeat) // BEATS_PER_BAR
        so, no = score_out(pa.sections, oa, nb_bars)
        for ki in ins:
            ib = (ki - b.downbeat) // BEATS_PER_BAR
            si, ni = score_in(pb.sections, ib, nb_bars)
            parts = {
                "out": so,
                "in": si,
                "clash": score_clash(pa, pb, oa, ib, nb_bars),
                "util_a": float(np.clip((oa / max(1, pa.n_bars) - PLAYED_MIN) / (PLAYED_FULL - PLAYED_MIN), 0.0, 1.0)),
                "util_b": 1.0 - ib / max(1, pb.n_bars),
            }
            out.append(Candidate(ko, ki, _combine(parts), parts, f"A {no}; B {ni}"))
    out.sort(key=lambda c: (-round(c.score, 6), -c.out_beat, c.in_beat))
    return out


def _latest_phrase_pick(a: TrackEntry, b: TrackEntry, overlap_beats: int, free_from: int,
                        phrase_bars: int) -> Candidate:
    """Milestone-1 behaviour and the fallback for short tracks: leave as late as possible, enter at the first
    downbeat."""
    last_ok = a.n_beats - 1 - overlap_beats
    cands = [k for k in a.grid.phrase_boundaries(a.duration_s, phrase_bars) if free_from <= k <= last_ok]
    if not cands:  # bar boundaries, then any beat
        every = range(free_from, last_ok + 1)
        cands = [k for k in every if a.grid.is_downbeat(k)] or list(every)
    if not cands:
        raise PlanError(f"'{a.id}' is too short for a {overlap_beats}-beat overlap into '{b.id}'")
    return Candidate(max(cands), b.downbeat, 0.0, {}, "latest phrase boundary (no scoring)")


def tempo_gap(a: TrackEntry, b: TrackEntry) -> float:
    """Fractional tempo change needed to match A to B (0.03 = 3%)."""
    return abs(b.bpm / a.bpm - 1.0)


def _track_id(i: int, path: str) -> str:
    return f"t{i + 1}-{Path(path).stem}"


def make_plan(analyses: list[TrackAnalysis], overlap_beats: int = 32, style: str = "bass_swap",
              phrase_bars: int = 8, sample_rate: int = 44100, bit_depth: int = 24,
              ceiling_db: float = -1.0, mix_points: str = "smart") -> MixPlan:
    """``mix_points``: ``"smart"`` scores candidates using the detected structure; ``"latest"`` leaves each track
    on its last phrase boundary that fits and enters the next at its first downbeat."""
    if mix_points not in ("smart", "latest"):
        raise ValueError("mix_points must be 'smart' or 'latest'")
    tracks = [
        TrackEntry(id=_track_id(i, a.path), path=str(Path(a.path).resolve()), bpm=round(a.bpm, 4),
                   offset_s=round(a.offset_s, 6), downbeat=a.downbeat, duration_s=round(a.duration_s, 4),
                   confidence=round(a.downbeat_confidence, 3), sections=list(a.sections))
        for i, a in enumerate(analyses)
    ]
    profiles = [Profile.from_analysis(a, max(0, (t.n_beats - t.downbeat) // BEATS_PER_BAR))
                for a, t in zip(analyses, tracks, strict=True)]
    transitions: list[Transition] = []
    free_from = 0  # first beat of the outgoing track that is clear of its own entrance
    for i in range(len(tracks) - 1):
        a, b = tracks[i], tracks[i + 1]
        ranked: list[Candidate] = []
        if mix_points == "smart":
            ranked = candidates(a, b, profiles[i], profiles[i + 1], overlap_beats, free_from, phrase_bars,
                                last_track=(i + 1 == len(tracks) - 1))
        best = ranked[0] if ranked else _latest_phrase_pick(a, b, overlap_beats, free_from, phrase_bars)
        transitions.append(Transition(
            a.id, b.id, best.out_beat, best.in_beat, overlap_beats, style, EQCurves(),
            score=round(best.score, 3) if ranked else None, note=best.note,
            alternatives=[c.to_dict() for c in ranked[1:1 + N_ALTERNATIVES]],
        ))
        free_from = best.in_beat + overlap_beats
    return check(MixPlan(tracks, transitions, sample_rate, bit_depth, ceiling_db))
