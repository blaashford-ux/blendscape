# SPDX-License-Identifier: GPL-3.0-or-later
"""Execute a mix plan: load, warp, split into bands, apply transition gains, sum, limit."""
from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

from .audio import load_audio
from .bands import Gains, bass_swap_gains, crossfade_gains, split3
from .limiter import apply_limiter
from .model import MixPlan, check
from .stretch import warp
from .timeline import Timeline, build_timeline

Progress = Callable[[str], None]
PAD_S = 0.5  # seconds of unity-speed context around each warped region


@dataclass
class RenderReport:
    duration_s: float = 0.0
    peak_before_db: float = 0.0  # peak of the summed mix before limiting, dBFS
    peak_after_db: float = 0.0
    max_reduction_db: float = 0.0  # deepest limiter gain reduction (>= 0)
    limited_fraction: float = 0.0  # share of the mix with more than 0.1 dB of reduction
    clipped: bool = False  # limiter off and the mix went over 0 dBFS (it will be clipped on export)
    transitions: list[tuple[float, float]] = field(default_factory=list)  # (start_s, end_s) of each overlap


def _beat_positions(master: np.ndarray, t: np.ndarray) -> np.ndarray:
    """Position of master samples ``t`` in overlap beats (0..N), piecewise linear through the beat map."""
    return np.interp(t, master, np.arange(len(master)))


def _render_track(plan: MixPlan, tl: Timeline, i: int, audio: np.ndarray) -> tuple[np.ndarray, int]:
    """Warp and gain-shape track ``i``; returns (audio (m, 2), master start sample)."""
    span = tl.spans[i]
    sr = plan.sample_rate
    n = audio.shape[0]
    # Rubber Band mis-places a stretched segment that begins at the very first sample it is given (about 16 ms
    # early at 3% stretch), so every region carries a little unity-speed context either side of the anchors.
    # The extra audio sits where the transition gains are zero (before the entrance / after the exit).
    pre = int(PAD_S * sr)
    s0 = max(0, int(round(span.src_start)) - pre)
    s1 = n if span.to_end_of_file else min(n, int(math.ceil(span.src_end)) + pre)
    region = audio[s0:s1]
    if len(span.anchors_src):
        y, start = warp(region, sr, span.anchors_src - s0, span.anchors_dst)
    else:  # nothing to stretch: just place
        y, start = region, int(round(s0 + span.shift))
    lo, mid, hi = split3(y, sr)
    bands = {"lo": lo, "mid": mid, "hi": hi}
    t = start + np.arange(len(y))
    gains: dict[str, np.ndarray] = {k: np.ones(len(y), np.float32) for k in bands}
    for j, trs in enumerate(tl.transitions):
        if j == i - 1:  # entering: gains of the incoming side
            g = _transition_gains(plan, trs, t)[1]
            m = t < trs.master[-1]
            for k in gains:
                gains[k] = np.where(m, g[k], gains[k])
        if j == i:  # leaving: gains of the outgoing side, silent afterwards
            g = _transition_gains(plan, trs, t)[0]
            m = t >= trs.master[0]
            for k in gains:
                gains[k] = np.where(m, g[k], gains[k])
    out = sum(bands[k] * gains[k][:, None] for k in bands)
    return out.astype(np.float32), start


def _transition_gains(plan: MixPlan, trs, t: np.ndarray) -> tuple[Gains, Gains]:
    tr = plan.transitions[trs.index]
    u = _beat_positions(trs.master, t)
    if trs.style == "crossfade":
        # master is just [start, end]; map linearly onto 0..N beats
        u = np.clip((t - trs.master[0]) / (trs.master[-1] - trs.master[0]), 0, 1) * trs.overlap_beats
        return crossfade_gains(u, trs.overlap_beats)
    return bass_swap_gains(u, trs.overlap_beats, tr.eq)


def render_parts(plan: MixPlan, progress: Progress | None = None) -> tuple[list[tuple[np.ndarray, int]], Timeline]:
    """Warp and gain-shape every track. Returns ``[(audio (m, 2), master start sample), ...]`` and the timeline."""
    check(plan)
    say = progress or (lambda _m: None)
    tl = build_timeline(plan)
    parts: list[tuple[np.ndarray, int]] = []
    for i, entry in enumerate(plan.tracks):
        say(f"[{i + 1}/{len(plan.tracks)}] {entry.id}: loading and warping")
        audio, _ = load_audio(entry.path, plan.sample_rate)
        parts.append(_render_track(plan, tl, i, audio))
        del audio
    return parts, tl


def render(plan: MixPlan, progress: Progress | None = None) -> tuple[np.ndarray, RenderReport]:
    parts, tl = render_parts(plan, progress)
    sr = plan.sample_rate
    n_out = max(start + len(y) for y, start in parts)
    mix = np.zeros((n_out, 2), np.float32)
    for y, start in parts:
        mix[start: start + len(y)] += y
    lim = plan.limiter
    say = progress or (lambda _m: None)
    report = RenderReport(duration_s=n_out / sr,
                          transitions=[tl.overlap_seconds(i) for i in range(len(tl.transitions))])
    if lim.enabled:
        say("limiting")
        mix, r = apply_limiter(mix, sr, plan.ceiling_db, lim.lookahead_ms, lim.release_ms, lim.true_peak)
        report.peak_before_db, report.peak_after_db = r.peak_before_db, r.peak_after_db
        report.max_reduction_db, report.limited_fraction = r.max_reduction_db, r.limited_fraction
    else:
        peak = float(np.abs(mix).max())
        report.peak_before_db = report.peak_after_db = 20 * math.log10(max(peak, 1e-9))
        if peak > 1.0:
            report.clipped = True
            mix = np.clip(mix, -1.0, 1.0)
    return mix, report
