# SPDX-License-Identifier: GPL-3.0-or-later
"""Plan -> timeline: where every track's beats land on the master (output) clock. Pure arithmetic, no audio.

Within an overlap both tracks are mapped beat-for-beat onto a master grid whose period glides linearly
from the outgoing track's period to the incoming track's (bass_swap style). Outside overlaps tracks play at
native speed, shifted so the overlap beats coincide."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .model import MixPlan, check


@dataclass
class TrackSpan:
    track_id: str
    src_start: float  # first source sample used (float; renderer floors)
    src_end: float  # one past the last source sample used (renderer ceils; None-like = to end)
    to_end_of_file: bool
    shift: float  # master = source + shift in the native stretches of this track
    anchors_src: np.ndarray = field(default_factory=lambda: np.zeros(0))
    anchors_dst: np.ndarray = field(default_factory=lambda: np.zeros(0))


@dataclass
class TransitionSpan:
    index: int
    style: str
    overlap_beats: int
    master: np.ndarray  # master sample position of overlap beat j, j = 0..overlap_beats


@dataclass
class Timeline:
    sample_rate: int
    spans: list[TrackSpan]
    transitions: list[TransitionSpan]

    def overlap_seconds(self, i: int) -> tuple[float, float]:
        m = self.transitions[i].master
        return m[0] / self.sample_rate, m[-1] / self.sample_rate


def build_timeline(plan: MixPlan) -> Timeline:
    check(plan)
    sr = plan.sample_rate
    spans = [TrackSpan(t.id, 0.0, 0.0, True, 0.0) for t in plan.tracks]
    tspans: list[TransitionSpan] = []
    for i, tr in enumerate(plan.transitions):
        a, b = plan.tracks[i], plan.tracks[i + 1]
        ga, gb = a.grid, b.grid
        N = tr.overlap_beats
        sa, sb = spans[i], spans[i + 1]
        # the outgoing track is at native speed at its out point, so its master position is source + shift
        src_out = ga.beat_sample(tr.out_beat, sr)
        m0 = src_out + sa.shift
        src_in = gb.beat_sample(tr.in_beat, sr)
        sb.src_start = src_in
        pa, pb = ga.period_s * sr, gb.period_s * sr
        if tr.style == "bass_swap":
            per = pa + (pb - pa) * (np.arange(N) + 0.5) / N
            master = m0 + np.concatenate([[0.0], np.cumsum(per)])
            out_src = [ga.beat_sample(tr.out_beat + j, sr) for j in range(N + 1)]
            sa.anchors_src = np.concatenate([sa.anchors_src, out_src])
            sa.anchors_dst = np.concatenate([sa.anchors_dst, master])
            sb.anchors_src = np.array([gb.beat_sample(tr.in_beat + j, sr) for j in range(N + 1)])
            sb.anchors_dst = master.copy()
            sb.shift = master[-1] - gb.beat_sample(tr.in_beat + N, sr)
            sa.src_end = ga.beat_sample(tr.out_beat + N, sr)
        else:  # crossfade: no stretching, B simply starts at A's out point
            master = np.array([m0, m0 + N * pa])
            sb.shift = m0 - src_in
            sa.src_end = ga.beat_sample(tr.out_beat, sr) + N * pa
        sa.to_end_of_file = False
        tspans.append(TransitionSpan(i, tr.style, N, master))
    return Timeline(sr, spans, tspans)
