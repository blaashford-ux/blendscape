# SPDX-License-Identifier: GPL-3.0-or-later
"""Objective checks for the blend spike (no ears needed): alignment, continuity, bass doubling, clipping."""
import sys, numpy as np, soundfile as sf
from scipy.signal import butter, sosfilt
import blend as BL
from analyze import analyze, load


def hp_env(x, sr, fc=5000, win_ms=1.0):
    sos = butter(4, fc, "high", fs=sr, output="sos")
    e = np.abs(sosfilt(sos, x.mean(1)))
    w = max(1, int(win_ms * 1e-3 * sr))
    return np.convolve(e, np.ones(w) / w, "same")


def lp_rms(x, sr, fc=200, win_s=0.5):
    sos = butter(4, fc, "low", fs=sr, output="sos")
    y = sosfilt(sos, x.mean(1))
    w = int(win_s * sr)
    n = len(y) // w
    return np.sqrt((y[: n * w].reshape(n, w) ** 2).mean(1))


def run(paths, beats, in_bar=0, out_bar=None, label=""):
    an = [analyze(p) for p in paths]
    sr = an[0]["sr"]
    cur, _ = load(paths[0])
    g = BL.grid_of(an[0], sr)
    for i, (a, p) in enumerate(zip(an[1:], paths[1:])):
        B, _ = load(p)
        cur, g, info = BL.blend(cur, g, B, BL.grid_of(a, sr), beats, out_bar, in_bar, sr)
        A_st, B_st, mixov = info["stems"]["A"], info["stems"]["B"], info["mixov"]
        # 1) alignment of the offbeat hats between the warped stems, per overlap beat
        eA, eB = hp_env(A_st, sr), hp_env(B_st, sr)
        P = g.period
        n_beat = len(eA) / beats
        offs = []
        for k in range(beats - 1):
            c = int((k + 0.5) * n_beat)
            w = int(0.12 * n_beat)
            sA, sB = eA[c - w:c + w], eB[c - w:c + w]
            if sA.max() < 1e-4 or sB.max() < 1e-4:
                continue
            thrA, thrB = 0.3 * sA.max(), 0.3 * sB.max()
            ia = int(np.argmax(sA > thrA)); ib = int(np.argmax(sB > thrB))
            offs.append((ia - ib) / sr * 1000)
        offs = np.array(offs)
        # 2) continuity at the overlap boundaries vs. typical sample-to-sample step
        full = cur
        s = int(info["overlap_start_s"] * sr); e = int(info["overlap_end_s"] * sr)
        d = np.abs(np.diff(full, axis=0)).max(1)
        base = np.percentile(d, 99.9)
        jump = max(d[s - 2000:s + 2000].max(), d[e - 2000:e + 2000].max())
        # 3) bass doubling: low-band RMS of the mix vs. louder of A-only / B-only, in 0.5 s windows
        rm, ra, rb = lp_rms(mixov, sr), lp_rms(A_st, sr), lp_rms(B_st, sr)
        ref = np.maximum(ra, rb)
        ok = ref > 1e-3
        ratio = (rm[ok] / ref[ok]) if ok.any() else np.array([0.0])
        print(f"[{label} transition {i+1}] BPM {an[i]['bpm']:.1f}->{an[i+1]['bpm']:.1f} overlap {beats} beats "
              f"({info['overlap_start_s']:.1f}-{info['overlap_end_s']:.1f}s)")
        if len(offs):
            print(f"   hat alignment A vs B in overlap: mean {offs.mean():+.2f} ms, std {offs.std():.2f}, "
                  f"max |{np.abs(offs).max():.2f}| ms over {len(offs)} beats")
        else:
            print("   hat alignment: n/a (no hats in both stems, e.g. A is in a breakdown)")
        print(f"   boundary step {jump:.4f} vs 99.9th pct step {base:.4f} -> {'OK' if jump < 3*base+0.05 else 'CHECK'}")
        print(f"   low-band level vs louder single track: max x{ratio.max():.2f} (mean x{ratio.mean():.2f})")
    peak = np.abs(cur).max()
    print(f"   mix peak {peak:.3f} ({'clips' if peak > 1 else 'no clipping'}), length {len(cur)/sr:.1f}s")
    return cur, sr


if __name__ == "__main__":
    f = "fixtures/"
    run([f + "a_124.wav", f + "b_128.wav"], 32, label="A->B")
    run([f + "a_124.wav", f + "b_128.wav", f + "c_126.wav"], 16, label="A->B->C")

    run([f + "a_124.wav", f + "b_128.wav"], 32, in_bar=8, out_bar=24, label="drop-swap A(break)->B(body)")
