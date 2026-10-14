# SPDX-License-Identifier: GPL-3.0-or-later
"""Varied synthetic club tracks with a known bar-line position, for testing downbeat detection.

Unlike ``synth.py`` (one pad accent per bar), each style hides the bar line in a different place, and some styles
contain decoys (an accent on beat 3), so no single cue is enough.

Truth convention matches the analyser: grid beat 0 is the first beat at or after t=0 (``offset_s``), and bar 1 starts
at grid beat ``downbeat`` (0..3). Bars before the first downbeat are generated too (a pick-up bar)."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import soundfile as sf

from synth import SR, hat, kick

STYLES = ("pad_accent", "bass_root", "backbeat", "decoy", "chords_only", "minimal")
# not used while tuning the detector: "flat" has no bar-line cue at all (the honest answer is "don't know"), and
# "conflict" has loud accents on beat 3 in both kick/bass and pad while only the chord change marks the real bar line
HARD_STYLES = ("flat", "conflict")
# 4-bar chord loop (Hz triads) used by the pad styles
CHORDS = [(220.0, 277.2, 329.6), (174.6, 220.0, 261.6), (196.0, 246.9, 293.7), (146.8, 185.0, 220.0)]
ROOTS = [55.0, 43.65, 49.0, 36.7]


@dataclass
class Truth:
    path: str
    style: str
    bpm: float
    offset_s: float
    downbeat: int


def _clap(rng):
    n = int(0.12 * SR)
    x = np.diff(rng.standard_normal(n), prepend=0)
    t = np.arange(n) / SR
    return (x * np.exp(-t * 35) * 0.5).astype(np.float64)


def _crash(rng):
    n = int(0.9 * SR)
    x = np.diff(rng.standard_normal(n), prepend=0)
    return (x * np.exp(-np.arange(n) / SR * 5) * 0.25).astype(np.float64)


def _tone(freqs, dur, amp, attack=0.01, decay=2.0):
    n = int(dur * SR)
    t = np.arange(n) / SR
    env = np.minimum(1, t / attack) * np.exp(-t / decay)
    return amp * env * sum(np.sin(2 * np.pi * f * t) for f in freqs)


def make_track(path, style, bpm, offset_s, downbeat, seed=0, n_bars=40):
    rng = np.random.default_rng(seed)
    period = 60.0 / bpm
    t0 = offset_s + downbeat * period  # start of bar 0 (first downbeat)
    total_s = t0 + n_bars * 4 * period + 2.0
    n = int(total_s * SR)
    buf = np.zeros((n, 2))
    k_s, h_s, c_s, cr_s = kick(), hat(), _clap(rng), _crash(rng)

    def put(sig, t, gain=1.0, pan=0.0):
        t += rng.normal(0, 0.002)  # a little human timing
        i = int(round(t * SR))
        if i < 0 or i >= n:
            return
        m = min(len(sig), n - i)
        g = gain * (1 + rng.normal(0, 0.06))
        buf[i:i + m, 0] += sig[:m] * g * (1 - pan)
        buf[i:i + m, 1] += sig[:m] * g * (1 + pan)

    for m in range(-1, n_bars):  # bar -1 is the pick-up bar
        mm = max(m, 0)
        in_intro = mm < 8
        bar_t = t0 + m * 4 * period
        chord = CHORDS[mm % 4]
        root = ROOTS[mm % 4]
        for q in range(4):
            t = bar_t + q * period
            off8 = t + period / 2
            if style == "pad_accent":
                if not in_intro:
                    put(k_s, t)
                put(h_s, off8)
            elif style == "bass_root":
                if not in_intro:
                    put(k_s, t)
                    put(_tone([root, 2 * root], 0.18 * period * 4 if q == 0 else 0.12, 0.45 if q == 0 else 0.0), t)
                    put(_tone([root * (1.5 if q % 2 else 1.0)], 0.1, 0.25), off8)
                put(h_s, off8)
            elif style == "backbeat":
                put(k_s, t)  # kick from the very first bar
                if q in (1, 3) and not in_intro:
                    put(c_s, t)
                put(h_s, off8)
                if not in_intro:
                    put(_tone([ROOTS[0]], 0.12, 0.3), off8)  # constant off-beat bass: no root information
            elif style == "decoy":
                if not in_intro:
                    put(k_s, t)
                    put(_tone([root, 2 * root], 0.15, 0.7 if q == 2 else 0.2), t)  # the loud accent is on beat 3
                put(h_s, off8)
            elif style == "chords_only":
                if not in_intro:
                    put(k_s, t)
                put(h_s, off8)
            elif style == "minimal":
                if not in_intro:
                    put(k_s, t)
                put(h_s, off8)
            elif style == "flat":
                put(k_s, t)
                put(h_s, off8)
            elif style == "conflict":
                put(k_s, t)
                put(h_s, off8)
                put(_tone([root, 2 * root], 0.15, 0.7 if q == 2 else 0.2), t)
                if q == 2:
                    put(_tone(chord, period, 0.12, 0.01, 0.5), t)  # pad stab on beat 3
        # per-bar events
        if style == "pad_accent":
            put(_tone(chord, 4 * period, 0.05, 0.02, 1.5), bar_t, 1.6 if mm % 4 == 0 else 1.0)
        elif style in ("decoy", "chords_only", "conflict"):
            put(_tone(chord, 4 * period, 0.06, 0.02, 1.5), bar_t)
        if style == "backbeat" and mm % 8 == 0 and m >= 0:
            put(cr_s, bar_t)
        if style == "minimal" and mm % 8 == 0 and m >= 0 and mm > 0:
            put(cr_s, bar_t, 0.5)
    buf += rng.normal(0, 0.002, buf.shape)  # room noise
    buf /= max(1.0, np.abs(buf).max() / 0.9)
    sf.write(path, buf.astype(np.float32), SR, subtype="PCM_16")
    return Truth(str(path), style, bpm, offset_s, downbeat)


def make_set(directory, per_style=6, seed=0, n_bars=40, styles=STYLES):
    """A balanced benchmark: every style at every bar-line position, with varied tempo/offset."""
    rng = np.random.default_rng(seed)
    out = []
    for si, style in enumerate(styles):
        for j in range(per_style):
            bpm = float(rng.choice([118, 122, 124, 126, 128, 130, 132, 136, 140]))
            period = 60.0 / bpm
            offset = float(rng.uniform(0.05, period - 0.05))
            db = (j + si) % 4
            out.append(make_track(f"{directory}/{style}_{j}.wav", style, bpm, offset, db, seed=1000 * si + j,
                                  n_bars=n_bars))
    return out
