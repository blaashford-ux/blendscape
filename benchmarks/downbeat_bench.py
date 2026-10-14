# SPDX-License-Identifier: GPL-3.0-or-later
"""Downbeat accuracy on the synthetic benchmark, using the true beat grid so only the bar-line choice is measured.

    python benchmarks/downbeat_bench.py [--per-style 6] [--bars 40] [--cues]
"""
import argparse
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

import librosa
import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

import synth_rich  # noqa: E402
from blendscape.downbeat import estimate_downbeat  # noqa: E402


def baseline(y, sr, offset, period):
    """The milestone-1 method: mid-band onset strength folded onto the four beat positions."""
    S = np.abs(librosa.stft(y, n_fft=1024, hop_length=64))
    f = librosa.fft_frequencies(sr=sr, n_fft=1024)
    m = (f >= 200) & (f < 800)
    L = np.log1p(30 * S[m])
    mid = np.maximum(0, np.diff(L, axis=1, prepend=L[:, :1])).sum(0)
    mid = mid / (np.percentile(mid, 99) + 1e-9)
    fps = sr / 64
    nb = int((len(y) / sr - offset) / period)
    w = int(0.02 * fps)
    mf = np.array([mid[max(0, int((offset + k * period) * fps) - w):int((offset + k * period) * fps) + w].max()
                   if int((offset + k * period) * fps) + w < len(mid) else 0 for k in range(nb)])
    return int(np.argmax([mf[p::4].mean() for p in range(4)]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-style", type=int, default=6)
    ap.add_argument("--bars", type=int, default=40)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--cues", action="store_true", help="print each cue's vote for every track")
    ap.add_argument("--hard", action="store_true", help="also run the styles that were not used while tuning")
    args = ap.parse_args()
    rows = []
    with tempfile.TemporaryDirectory() as d:
        styles = synth_rich.STYLES + (synth_rich.HARD_STYLES if args.hard else ())
        truth = synth_rich.make_set(d, args.per_style, args.seed, args.bars, styles)
        for t in truth:
            y, sr = sf.read(t.path, dtype="float32", always_2d=True)
            y = y.mean(1)
            period = 60 / t.bpm
            est = estimate_downbeat(y, sr, t.offset_s, period)
            rows.append((t, baseline(y, sr, t.offset_s, period), est))
            if args.cues:
                print(Path(t.path).name, "truth", t.downbeat, "->", est.phase, f"conf {est.confidence:.2f}")
                for k, v in est.cues.items():
                    print(f"      {k:13s} {v}")
    by = defaultdict(lambda: [0, 0, 0])
    for t, b, e in rows:
        by[t.style][0] += 1
        by[t.style][1] += b == t.downbeat
        by[t.style][2] += e.phase == t.downbeat
    print(f"{'style':13s} {'n':>3s} {'baseline':>9s} {'new':>6s}")
    tot = [0, 0, 0]
    for style in styles:
        n, b, e = by[style]
        print(f"{style:13s} {n:3d} {b / n:9.0%} {e / n:6.0%}")
        tot = [tot[0] + n, tot[1] + b, tot[2] + e]
    print(f"{'ALL':13s} {tot[0]:3d} {tot[1] / tot[0]:9.0%} {tot[2] / tot[0]:6.0%}")
    # calibration: does confidence mean anything?
    print("\nconfidence  tracks  accuracy")
    edges = [0.25, 0.5, 0.7, 0.9, 1.01]
    for lo, hi in zip(edges[:-1], edges[1:], strict=True):
        sel = [e.phase == t.downbeat for t, _, e in rows if lo <= e.confidence < hi]
        if sel:
            print(f"{lo:.2f}-{min(hi, 1):.2f}  {len(sel):6d}  {np.mean(sel):8.0%}")


if __name__ == "__main__":
    main()
