# SPDX-License-Identifier: GPL-3.0-or-later
"""Spike: beat-matched, phase-aligned, 3-band-EQ-swap blend of an ordered list of wav files.

Usage: python blend.py out.wav a.wav b.wav [c.wav ...] [--beats 32]
"""
import sys, json, argparse, os
import numpy as np, soundfile as sf
from scipy.signal import butter, sosfilt
from analyze import analyze, load
from warp import warp

XO = (200.0, 2000.0)  # crossover frequencies (Hz)


# ---------- band split (Linkwitz-Riley 4th order = two cascaded Butterworth 2nd order) ----------
def _lr4(fc, sr, kind):
    sos = butter(2, fc, kind, fs=sr, output="sos")
    return np.vstack([sos, sos])


def split3(x, sr):
    lo = sosfilt(_lr4(XO[0], sr, "low"), x, axis=0)
    rest = sosfilt(_lr4(XO[0], sr, "high"), x, axis=0)
    mid = sosfilt(_lr4(XO[1], sr, "low"), rest, axis=0)
    hi = sosfilt(_lr4(XO[1], sr, "high"), rest, axis=0)
    return lo, mid, hi


# ---------- curves ----------
def ramp(t, a, b):
    """0 before a, 1 after b, raised-cosine between (times in the same unit as t)."""
    u = np.clip((t - a) / max(b - a, 1e-9), 0, 1)
    return 0.5 - 0.5 * np.cos(np.pi * u)


class Grid:
    """beat k is at offset + k*period (samples); bar 1 starts at beat index `db` (mod 4)."""

    def __init__(self, offset, period, db):
        self.offset, self.period, self.db = offset, period, db

    def beat(self, k):
        return self.offset + k * self.period


def blend(A, ga, B, gb, n_beats, out_bar=None, in_bar=0, sr=44100):
    """A: audio (n,ch) of the running mix with tail grid ga; B: new track with grid gb.
    out_bar: bar index (in A's grid) where the overlap starts (default: last 8-bar boundary that fits).
    in_bar: bar index in B where it enters. Returns (mix, tail grid, info)."""
    nb = n_beats
    # --- choose mix points on the phrase grid ---
    a_last_beat = int((len(A) - ga.offset) / ga.period)
    if out_bar is None:
        k = (a_last_beat - nb - ga.db) // 32 * 32 + ga.db  # latest 8-bar boundary with the whole overlap inside A
        a_out = k
    else:
        a_out = ga.db + 4 * out_bar
    b_in = gb.db + 4 * in_bar
    Pa, Pb = ga.period, gb.period
    # --- master tempo curve over the overlap: period glides linearly Pa -> Pb, per beat ---
    per = Pa + (Pb - Pa) * (np.arange(nb) + 0.5) / nb
    M = ga.beat(a_out) + np.concatenate([[0], np.cumsum(per)])  # master time of overlap beat k=0..nb (samples)
    # --- warp A (keep everything before the overlap untouched) and B (starts at its mix-in point) ---
    a_src = np.array([ga.beat(a_out + k) for k in range(nb + 1)])
    Aw = warp(A, sr, a_src, M)
    b_src = np.array([gb.beat(b_in + k) for k in range(nb + 1)])
    Bw = warp(B, sr, b_src, M)
    # B before b_in is not part of the mix
    t0, t1 = int(round(M[0])), int(round(M[-1]))
    Bw[:t0] = 0
    n = max(len(Aw), len(Bw))
    Aw = np.pad(Aw, ((0, n - len(Aw)), (0, 0)))
    Bw = np.pad(Bw, ((0, n - len(Bw)), (0, 0)))
    # --- 3-band EQ curves in units of overlap beats ---
    t = np.arange(n)
    u = (t - M[0]) / ((M[-1] - M[0]) / nb)  # position in overlap beats (nonlinear in time but close; ok for curves)
    half = nb / 2
    swap = ramp(u, half - 2, half + 2)                # bass swap over 4 beats at the middle
    gA = dict(lo=1 - swap, mid=1 - ramp(u, 0.45 * nb, 0.85 * nb), hi=1 - ramp(u, 0.60 * nb, 1.0 * nb))
    gB = dict(lo=swap, mid=ramp(u, 0.15 * nb, 0.60 * nb), hi=ramp(u, 0.0, 0.40 * nb))
    la, ma, ha = split3(Aw, sr)
    lb, mb, hb = split3(Bw, sr)
    g = lambda d, k: d[k][:, None]
    mix = (la * g(gA, "lo") + ma * g(gA, "mid") + ha * g(gA, "hi") +
           lb * g(gB, "lo") + mb * g(gB, "mid") + hb * g(gB, "hi"))
    # outside the overlap A is untouched before, B untouched after (curves are 1/0 there already)
    info = dict(a_out_bar_beat=int(a_out), b_in_beat=int(b_in), overlap_start_s=M[0] / sr, overlap_end_s=M[-1] / sr,
                stems=dict(A=Aw[t0:t1], B=Bw[t0:t1]), mixov=mix[t0:t1], eq=dict(gA=gA, gB=gB))
    # tail grid: B's beats after the overlap sit at M[-1] + j*Pb
    tail_off = M[-1] - (b_in + nb) * Pb
    tail_db = gb.db
    return mix, Grid(tail_off, Pb, tail_db), info


def grid_of(a, sr):
    return Grid(a["offset_s"] * sr, a["period_s"] * sr, a["downbeat"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("tracks", nargs="+")
    ap.add_argument("--beats", type=int, default=32)
    ap.add_argument("--in-bar", type=int, default=0)
    ap.add_argument("--out-bar", type=int, default=None)
    ap.add_argument("--report", default=None)
    args = ap.parse_args()
    an = [analyze(p) for p in args.tracks]
    for a in an:
        print(f'{a["path"]}: {a["bpm"]:.2f} BPM, downbeat idx {a["downbeat"]} (conf {a["downbeat_conf"]:.2f})')
    sr = an[0]["sr"]
    cur, _ = load(args.tracks[0])
    g = grid_of(an[0], sr)
    infos = []
    for a, p in zip(an[1:], args.tracks[1:]):
        B, _ = load(p)
        cur, g, info = blend(cur, g, B, grid_of(a, sr), args.beats, args.out_bar, args.in_bar, sr)
        infos.append(info)
        print(f'  overlap {info["overlap_start_s"]:.2f}s -> {info["overlap_end_s"]:.2f}s')
    peak = np.abs(cur).max()
    if peak > 0.98:
        cur = cur * (0.98 / peak)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    sf.write(args.out, cur.astype(np.float32), sr, subtype="PCM_16")
    print(f'wrote {args.out}: {len(cur)/sr:.1f}s, peak {peak:.3f}')
    return cur, infos, sr


if __name__ == "__main__":
    main()
