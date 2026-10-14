# SPDX-License-Identifier: GPL-3.0-or-later
"""Look-ahead true-peak limiter for the final mix.

Offline (the whole mix is in memory), so the gain curve can look both ways and needs no latency compensation:

1. ``peak[n]``      stereo-linked peak at sample ``n``, measured on a 4x oversampled signal so inter-sample
                    peaks count ("true peak").
2. ``need[n]``      the gain that would bring that peak to the ceiling (1 where it is already below).
3. attack           sliding minimum of ``need`` over +-lookahead, then a moving average over the same window. Every
                    point inside the window has ``need`` >= the minimum around it, so the averaged gain is never
                    above ``need[n]``: the ceiling cannot be exceeded, and gain reductions arrive as smooth ramps
                    that finish before the peak.
4. release          the reduction (1 - gain) decays exponentially after a peak, ``reduction[n] = max(wanted[n],
                    reduction[n-1] * decay)``, which only ever lowers the gain further, so the guarantee holds.

Gain is applied equally to both channels, so the stereo image is untouched, and signal that never nears the ceiling
passes through bit-for-bit.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numba import njit
from scipy.ndimage import minimum_filter1d, uniform_filter1d
from scipy.signal import resample_poly

OVERSAMPLE = 4
_TP_CHUNK = 1 << 19
_TP_MARGIN = 128  # samples of context either side of a chunk for the interpolation filter
_QUIET = 0.4  # chunks peaking below 40% of the ceiling (-8 dB) cannot hide an over; skip their oversampling


@dataclass(frozen=True)
class LimiterReport:
    peak_before_db: float  # true peak (or sample peak) before limiting, dBFS
    peak_after_db: float
    max_reduction_db: float  # deepest gain reduction applied (>= 0)
    limited_fraction: float  # share of the mix with more than 0.1 dB of reduction


def _db(x: float) -> float:
    return 20.0 * np.log10(max(float(x), 1e-9))


def peak_envelope(x: np.ndarray, ceiling: float, true_peak: bool = True, chunk: int = _TP_CHUNK) -> np.ndarray:
    """Per-sample stereo-linked peak ``(n,)``; with ``true_peak`` the maximum of the oversampled signal around
    each sample. ``ceiling`` (linear) only controls which quiet stretches can skip the oversampling."""
    sample = np.abs(x).max(axis=1)
    if not true_peak:
        return sample
    out = sample.copy()
    n = len(x)
    for start in range(0, n, chunk):
        end = min(n, start + chunk)
        if sample[start:end].max() < _QUIET * ceiling:
            continue
        lo, hi = max(0, start - _TP_MARGIN), min(n, end + _TP_MARGIN)
        up = resample_poly(x[lo:hi].astype(np.float64), OVERSAMPLE, 1, axis=0)
        env = np.abs(up).max(axis=1)[: (hi - lo) * OVERSAMPLE].reshape(-1, OVERSAMPLE).max(axis=1)
        out[start:end] = np.maximum(sample[start:end], env[start - lo: end - lo])
    return out


@njit(cache=True)
def _release(wanted: np.ndarray, decay: float) -> np.ndarray:
    out = np.empty_like(wanted)
    prev = 0.0
    for i in range(wanted.shape[0]):
        prev = max(wanted[i], prev * decay)
        out[i] = prev
    return out


def gain_curve(peak: np.ndarray, sr: int, ceiling: float, lookahead_ms: float = 3.0,
               release_ms: float = 120.0) -> np.ndarray | None:
    """Linear gain per sample (<= 1) that keeps ``peak`` under ``ceiling``; ``None`` if nothing needs limiting."""
    if peak.max() <= ceiling:
        return None
    need = np.minimum(1.0, ceiling / np.maximum(peak, 1e-12))
    a = max(1, int(round(lookahead_ms * 1e-3 * sr)))
    window = 2 * a + 1
    smooth = uniform_filter1d(minimum_filter1d(need, window, mode="nearest"), window, mode="nearest")
    decay = float(np.exp(-1.0 / max(1.0, release_ms * 1e-3 * sr)))
    reduction = _release(1.0 - smooth, decay)
    return 1.0 - reduction


def apply_limiter(x: np.ndarray, sr: int, ceiling_db: float = -1.0, lookahead_ms: float = 3.0,
                  release_ms: float = 120.0, true_peak: bool = True) -> tuple[np.ndarray, LimiterReport]:
    """Limit ``x`` (n, ch) to ``ceiling_db``. Returns the limited audio and a report."""
    ceiling = 10 ** (ceiling_db / 20)
    peak = peak_envelope(x, ceiling, true_peak)
    before = float(peak.max()) if len(peak) else 0.0
    g = gain_curve(peak, sr, ceiling, lookahead_ms, release_ms)
    if g is None:
        return x, LimiterReport(_db(before), _db(before), 0.0, 0.0)
    y = (x * g.astype(np.float32)[:, None]).astype(np.float32)
    after = float(peak_envelope(y, ceiling, true_peak).max())
    return y, LimiterReport(
        peak_before_db=_db(before), peak_after_db=_db(after),
        max_reduction_db=-_db(float(g.min())), limited_fraction=float(np.mean(g < 0.9886)),
    )
