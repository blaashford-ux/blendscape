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


def render_preview(plan: MixPlan, index: int, lead_s: float = 8.0, tail_s: float = 8.0,
                   progress: Progress | None = None) -> tuple[np.ndarray, int, RenderReport]:
    """Render just transition ``index`` (0-based) with ``lead_s`` seconds of the outgoing track before the overlap and
    ``tail_s`` seconds of the incoming track after it, through the same limiter as the full mix. The outgoing track
    plays at its native tempo before the overlap, whatever came before it in the full plan.
    Returns ``(audio, sample_rate, report)``."""
    check(plan)
    if not 0 <= index < len(plan.transitions):
        raise ValueError(f"no transition {index}")
    say = progress or (lambda _m: None)
    sub = MixPlan(tracks=[plan.tracks[index], plan.tracks[index + 1]], transitions=[plan.transitions[index]],
                sample_rate=plan.sample_rate, bit_depth=plan.bit_depth, ceiling_db=plan.ceiling_db,
                limiter=plan.limiter)
    tl = build_timeline(sub)
    sr = plan.sample_rate
    master = tl.transitions[0].master
    w0 = max(0, int(master[0] - lead_s * sr))
    w1 = int(master[-1] + tail_s * sr)
    a_span, b_span = tl.spans
    a_span.src_start = max(0.0, w0 - a_span.shift)  # a is the first track of the sub-plan: shift 0
    b_span.src_end = w1 - b_span.shift
    b_span.to_end_of_file = False
    mix = np.zeros((w1 - w0, 2), np.float32)
    for i, entry in enumerate(sub.tracks):
        say(f"[{i + 1}/2] {entry.id}: loading and warping")
        audio, _ = load_audio(entry.path, sr)
        y, start = _render_track(sub, tl, i, audio)
        del audio
        lo, hi = max(start, w0), min(start + len(y), w1)
        if hi > lo:
            mix[lo - w0: hi - w0] += y[lo - start: hi - start]
    report = RenderReport(duration_s=len(mix) / sr, transitions=[((master[0] - w0) / sr, (master[-1] - w0) / sr)])
    mix = _finish(mix, sub, report, say)
    return mix, sr, report


def _finish(mix: np.ndarray, plan: MixPlan, report: RenderReport, say: Progress) -> np.ndarray:
    """Limit (or clip) the summed mix and fill in the level fields of ``report``."""
    sr, lim = plan.sample_rate, plan.limiter
    if lim.enabled:
        say("limiting")
        mix, r = apply_limiter(mix, sr, plan.ceiling_db, lim.lookahead_ms, lim.release_ms, lim.true_peak)
        report.peak_before_db, report.peak_after_db = r.peak_before_db, r.peak_after_db
        report.max_reduction_db, report.limited_fraction = r.max_reduction_db, r.limited_fraction
    else:
        peak = float(np.abs(mix).max()) if len(mix) else 0.0
        report.peak_before_db = report.peak_after_db = 20 * math.log10(max(peak, 1e-9))
        if peak > 1.0:
            report.clipped = True
            mix = np.clip(mix, -1.0, 1.0)
    return mix


def render(plan: MixPlan, progress: Progress | None = None) -> tuple[np.ndarray, RenderReport]:
    parts, tl = render_parts(plan, progress)
    sr = plan.sample_rate
    n_out = max(start + len(y) for y, start in parts)
    mix = np.zeros((n_out, 2), np.float32)
    for y, start in parts:
        mix[start: start + len(y)] += y
    say = progress or (lambda _m: None)
    report = RenderReport(duration_s=n_out / sr,
                          transitions=[tl.overlap_seconds(i) for i in range(len(tl.transitions))])
    mix = _finish(mix, plan, report, say)
    return mix, report
