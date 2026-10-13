# SPDX-License-Identifier: GPL-3.0-or-later
"""Synthetic dance-track generator with known BPM / first-downbeat offset (test fixture).

Sections: intro (hats+pad) | body (kick+bass+hats+pad) | break (pad only) | outro (kick+hats fading). 4/4.
Bar 1 of every phrase carries an accented chord stab so the downbeat is audible.
"""
import numpy as np
import soundfile as sf

SR = 44100


def _env(n, a, d):
    t = np.arange(n) / SR
    return np.exp(-t / d) * np.minimum(1, t / a)


def kick(n=int(0.25 * SR)):
    t = np.arange(n) / SR
    f = 50 + 110 * np.exp(-t * 28)
    ph = 2 * np.pi * np.cumsum(f) / SR
    return np.sin(ph) * np.exp(-t * 14) * 0.9


def hat(n=int(0.05 * SR)):
    x = np.random.default_rng(1).standard_normal(n)
    x = np.diff(x, prepend=0)  # crude highpass
    return x * np.exp(-np.arange(n) / SR * 90) * 0.25


PLAN_SHORT = [("intro", 8), ("body", 8), ("outro", 8)]
PLAN_FULL = [("intro", 8), ("body", 16), ("break", 8), ("body", 16), ("outro", 8)]


def make_track(path, bpm, offset_s, key_hz=55.0, seed=0, plan=None):
    plan = plan or PLAN_SHORT
    total_bars = sum(b for _, b in plan)
    beat = 60.0 / bpm
    n = int((offset_s + total_bars * 4 * beat + 2) * SR)
    L = np.zeros(n)
    R = np.zeros(n)
    k, h = kick(), hat()
    bar = 0
    for name, nb in plan:
        for b in range(nb):
            for q in range(4):
                t0 = offset_s + (bar * 4 + q) * beat
                i = int(round(t0 * SR))
                gain = 1.0
                if name == "outro":
                    gain = 1 - b / nb
                if name in ("body", "outro"):
                    L[i:i + len(k)] += k[:n - i] * gain
                    R[i:i + len(k)] += k[:n - i] * gain
                if name != "break":
                    j = int(round((t0 + beat / 2) * SR))  # offbeat hat
                    L[j:j + len(h)] += h * gain
                    R[j:j + len(h)] += h * gain * 0.9
                if name == "body":
                    # rolling bass on the off-beat 8ths
                    for e in (0.5,):
                        jb = int(round((t0 + e * beat) * SR))
                        m = int(beat * 0.45 * SR)
                        t = np.arange(m) / SR
                        s = np.sin(2 * np.pi * key_hz * (1 if q % 2 == 0 else 1.5) * t) * _env(m, 0.005, 0.2) * 0.45
                        L[jb:jb + m] += s[:n - jb]
                        R[jb:jb + m] += s[:n - jb]
            # pad chord over the bar, accented stab at bar 1 of each 8-bar phrase
            t0 = offset_s + bar * 4 * beat
            i = int(round(t0 * SR))
            m = int(4 * beat * SR)
            t = np.arange(m) / SR
            acc = 1.6 if b % 8 == 0 else 1.0
            pad = sum(np.sin(2 * np.pi * key_hz * r * t) for r in (4, 5, 6)) * 0.05 * acc * _env(m, 0.02, 1.5)
            L[i:i + m] += pad[:n - i]
            R[i:i + m] += pad[:n - i] * 0.95
            bar += 1
    out = np.stack([L, R], 1)
    out /= max(1.0, np.abs(out).max() / 0.9)
    sf.write(path, out.astype(np.float32), SR, subtype="PCM_16")
    return dict(path=str(path), bpm=bpm, offset=offset_s, bars=total_bars, plan=plan)
