# SPDX-License-Identifier: GPL-3.0-or-later
"""Song-structure detection from per-bar band loudness: intro / body / breakdown / outro.

Deliberately simple and explainable: find bars where the spectral balance or loudness changes sharply, snap those
boundaries to the phrase grid, then label each stretch by how loud it is relative to the loud parts of the track."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

LABELS = ("intro", "body", "breakdown", "outro")


@dataclass(frozen=True)
class Section:
    label: str
    start_bar: int  # inclusive; bar 0 starts at the track's first downbeat
    end_bar: int  # exclusive

    @property
    def bars(self) -> int:
        return self.end_bar - self.start_bar

    def to_dict(self) -> dict:
        return {"label": self.label, "start_bar": self.start_bar, "end_bar": self.end_bar}

    @classmethod
    def from_dict(cls, d: dict) -> Section:
        return cls(d["label"], int(d["start_bar"]), int(d["end_bar"]))


def _db(x: np.ndarray) -> np.ndarray:
    return 20 * np.log10(np.maximum(x, 1e-4))


def _normalise(v: np.ndarray) -> np.ndarray:
    """Scale a dB series to 0..1 by its robust range; a band that barely moves contributes a constant."""
    lo, hi = np.percentile(v, 5), np.percentile(v, 95)
    if hi - lo < 6.0:
        return np.full_like(v, 0.5)
    return np.clip((v - lo) / (hi - lo), 0.0, 1.0)


def _novelty(feats: np.ndarray, w: int) -> np.ndarray:
    """Mean absolute difference between the average features of the ``w`` bars after and before each bar line."""
    n = feats.shape[0]
    csum = np.vstack([np.zeros((1, feats.shape[1])), np.cumsum(feats, axis=0)])
    out = np.zeros(n)
    for b in range(w, n - w + 1):
        after = (csum[b + w] - csum[b]) / w
        before = (csum[b] - csum[b - w]) / w
        out[b] = float(np.abs(after - before).mean())
    return out


def _pick_boundaries(nov: np.ndarray, thr: float, min_gap: int) -> list[int]:
    peaks = [b for b in range(1, len(nov) - 1) if nov[b] >= thr and nov[b] >= nov[b - 1] and nov[b] > nov[b + 1]]
    chosen: list[int] = []
    for b in sorted(peaks, key=lambda p: -nov[p]):
        if all(abs(b - c) >= min_gap for c in chosen):
            chosen.append(b)
    return sorted(chosen)


def detect_sections(low, mid, high, phrase_bars: int = 8, min_bars: int = 4) -> list[Section]:
    """Segment a track from per-bar RMS of its low, mid and high bands (bar 0 = first downbeat)."""
    low, mid, high = (np.asarray(x, dtype=float) for x in (low, mid, high))
    n = len(low)
    if n == 0:
        return []
    if n < 2 * min_bars:
        return [Section("body", 0, n)]
    bands_db = [_db(low), _db(mid), _db(high)]
    feats = np.stack([_normalise(b) for b in bands_db], axis=1)
    energy = 10 * np.log10(low**2 + mid**2 + high**2 + 1e-8)

    w = max(2, min(4, n // 4))
    nov = _novelty(feats, w)
    thr = max(0.2, 0.3 * float(nov.max()))
    bounds = _pick_boundaries(nov, thr, min_bars)
    snapped = []
    for b in bounds:
        m = round(b / phrase_bars) * phrase_bars
        snapped.append(m if abs(b - m) <= 2 and 0 < m < n else b)
    edges = sorted({0, n, *snapped})
    # drop slivers created by snapping
    edges = [e for i, e in enumerate(edges) if i == 0 or e - edges[i - 1] >= min_bars or e == n]
    if edges[-1] - edges[-2] < min_bars and len(edges) > 2:
        edges.pop(-2)
    segs = [(a, b) for a, b in zip(edges[:-1], edges[1:], strict=True)]

    audible = energy[energy > -70]
    ref = float(np.percentile(audible, 75)) if len(audible) else float(energy.max())
    mean_e = [float(energy[a:b].mean()) for a, b in segs]
    loud = [e >= ref - 4.5 for e in mean_e]

    labels = []
    for i, (a, b) in enumerate(segs):
        if i == 0 and len(segs) > 1:
            label = "body" if loud[i] else "intro"
        elif i == len(segs) - 1 and len(segs) > 1:
            q = max(1, (b - a) // 4)
            falling = float(energy[b - q: b].mean()) < float(energy[a: a + q].mean()) - 3.0
            label = "outro" if (not loud[i] or (b - a <= 16 and falling)) else "body"
        else:
            label = "body" if loud[i] else "breakdown"
        labels.append(label)

    merged: list[Section] = []
    for (a, b), label in zip(segs, labels, strict=True):
        if merged and merged[-1].label == label:
            merged[-1] = Section(label, merged[-1].start_bar, b)
        else:
            merged.append(Section(label, a, b))
    return merged


def section_at(sections: list[Section], bar: int) -> Section | None:
    for s in sections:
        if s.start_bar <= bar < s.end_bar:
            return s
    return None


def is_drop(sections: list[Section], i: int) -> bool:
    """True if section ``i`` is a body that follows an intro or breakdown (the energy comes back in)."""
    return i > 0 and sections[i].label == "body" and sections[i - 1].label in ("intro", "breakdown")
