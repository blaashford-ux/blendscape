# SPDX-License-Identifier: GPL-3.0-or-later
"""Build a default mix plan from analysed tracks (section-aware mix-point scoring comes in milestone 2)."""
from __future__ import annotations

from pathlib import Path

from .analysis import TrackAnalysis
from .model import EQCurves, MixPlan, PlanError, TrackEntry, Transition, check


def _track_id(i: int, path: str) -> str:
    return f"t{i + 1}-{Path(path).stem}"


def make_plan(analyses: list[TrackAnalysis], overlap_beats: int = 32, style: str = "bass_swap",
              phrase_bars: int = 8, sample_rate: int = 44100, bit_depth: int = 24,
              ceiling_db: float = -1.0) -> MixPlan:
    """Default placement: the outgoing track leaves on its latest phrase boundary that still fits the whole
    overlap (after it has finished entering); the incoming track enters at its first downbeat."""
    tracks = [
        TrackEntry(id=_track_id(i, a.path), path=str(Path(a.path).resolve()), bpm=round(a.bpm, 4),
                   offset_s=round(a.offset_s, 6), downbeat=a.downbeat, duration_s=round(a.duration_s, 4),
                   confidence=round(a.downbeat_confidence, 3))
        for i, a in enumerate(analyses)
    ]
    transitions: list[Transition] = []
    free_from = 0  # first beat of the outgoing track that is clear of its own entrance
    for i in range(len(tracks) - 1):
        a, b = tracks[i], tracks[i + 1]
        last_ok = a.n_beats - 1 - overlap_beats
        cands = [k for k in a.grid.phrase_boundaries(a.duration_s, phrase_bars) if free_from <= k <= last_ok]
        if not cands:  # fall back to bar boundaries, then to any beat
            cands = [k for k in range(free_from, last_ok + 1) if a.grid.is_downbeat(k)] or \
                    list(range(free_from, last_ok + 1))
        if not cands:
            raise PlanError(f"'{a.id}' is too short for a {overlap_beats}-beat overlap into '{b.id}'")
        out_beat = max(cands)
        in_beat = b.downbeat
        transitions.append(Transition(a.id, b.id, out_beat, in_beat, overlap_beats, style, EQCurves()))
        free_from = in_beat + overlap_beats
    return check(MixPlan(tracks, transitions, sample_rate, bit_depth, ceiling_db))
