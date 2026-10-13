# SPDX-License-Identifier: GPL-3.0-or-later
"""Three-band Linkwitz-Riley split and the per-band gain curves of a transition."""
from __future__ import annotations

import numpy as np
from scipy.signal import butter, sosfilt

from .model import EQCurves

CROSSOVERS_HZ = (200.0, 2000.0)


def _lr4(fc: float, sr: int, kind: str) -> np.ndarray:
    sos = butter(2, fc, kind, fs=sr, output="sos")  # two cascaded 2nd-order Butterworths = LR4
    return np.vstack([sos, sos])


def split3(x: np.ndarray, sr: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Split (n, ch) audio into low / mid / high. The three bands sum back to an all-pass copy of ``x``
    (flat magnitude), so unity band gains reproduce the signal's level."""
    lo_c, hi_c = CROSSOVERS_HZ
    lo = sosfilt(_lr4(lo_c, sr, "low"), x, axis=0)
    rest = sosfilt(_lr4(lo_c, sr, "high"), x, axis=0)
    mid = sosfilt(_lr4(hi_c, sr, "low"), rest, axis=0)
    hi = sosfilt(_lr4(hi_c, sr, "high"), rest, axis=0)
    return lo.astype(np.float32), mid.astype(np.float32), hi.astype(np.float32)


def ramp(u: np.ndarray, a: float, b: float) -> np.ndarray:
    """0 before ``a``, 1 after ``b``, raised cosine in between."""
    t = np.clip((np.asarray(u, dtype=float) - a) / max(b - a, 1e-9), 0.0, 1.0)
    return 0.5 - 0.5 * np.cos(np.pi * t)


Gains = dict[str, np.ndarray]  # keys: lo, mid, hi


def bass_swap_gains(u: np.ndarray, n_beats: int, eq: EQCurves) -> tuple[Gains, Gains]:
    """Per-band gains for the outgoing (A) and incoming (B) track. ``u`` is the position in overlap beats
    (0 at the start of the overlap, ``n_beats`` at its end)."""
    c = eq.bass_swap_center * n_beats
    h = eq.bass_swap_beats / 2.0
    swap = ramp(u, c - h, c + h)
    g_a = {
        "lo": 1 - swap,
        "mid": 1 - ramp(u, eq.mid_out[0] * n_beats, eq.mid_out[1] * n_beats),
        "hi": 1 - ramp(u, eq.hi_out[0] * n_beats, eq.hi_out[1] * n_beats),
    }
    g_b = {
        "lo": swap,
        "mid": ramp(u, eq.mid_in[0] * n_beats, eq.mid_in[1] * n_beats),
        "hi": ramp(u, eq.hi_in[0] * n_beats, eq.hi_in[1] * n_beats),
    }
    return g_a, g_b


def crossfade_gains(u: np.ndarray, n_beats: int) -> tuple[Gains, Gains]:
    """Equal-power crossfade, identical on every band (the fallback when beat-matching is not trusted)."""
    t = np.clip(np.asarray(u, dtype=float) / n_beats, 0.0, 1.0)
    a, b = np.cos(t * np.pi / 2), np.sin(t * np.pi / 2)
    return ({k: a for k in ("lo", "mid", "hi")}, {k: b for k in ("lo", "mid", "hi")})
