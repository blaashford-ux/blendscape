# SPDX-License-Identifier: GPL-3.0-or-later
"""Listen-and-verify helper: lay a metronome over the music, high click on the detected bar line, low clicks between.

Beat positions and the bar line are the two things the blend relies on; both are easy to judge by ear and hard to judge
by looking at numbers."""
from __future__ import annotations

import numpy as np

from .analysis import TrackAnalysis

HIGH_HZ, LOW_HZ = 1568.0, 784.0


def _click(sr: int, hz: float, amp: float) -> np.ndarray:
    n = int(0.03 * sr)
    t = np.arange(n) / sr
    return (amp * np.sin(2 * np.pi * hz * t) * np.exp(-t / 0.008)).astype(np.float32)


def default_start_s(a: TrackAnalysis) -> float:
    """A bar line near the first loud section, so the clicks sit over a clear beat."""
    period = 60.0 / a.bpm
    bar = 0
    for s in a.sections:
        if s["label"] == "body":
            bar = s["start_bar"]
            break
    return a.offset_s + (a.downbeat + 4 * bar) * period


def click_check(a: TrackAnalysis, audio: np.ndarray, start_s: float | None = None, seconds: float = 30.0,
                music_gain: float = 0.7) -> tuple[np.ndarray, float]:
    """Return ``(stereo audio with clicks, start_s)`` for ``seconds`` of ``audio`` (n, 2) from ``start_s``.
    ``start_s`` is moved back to the nearest bar line so the first click is a "one"."""
    sr = a.sample_rate
    period = 60.0 / a.bpm
    start = default_start_s(a) if start_s is None else start_s
    k0 = round((start - a.offset_s) / period)
    k0 -= (k0 - a.downbeat) % 4  # snap back to a bar line
    start = a.offset_s + k0 * period
    i0 = max(0, int(start * sr))
    i1 = min(len(audio), i0 + int(seconds * sr))
    seg = audio[i0:i1] * music_gain
    high, low = _click(sr, HIGH_HZ, 0.55), _click(sr, LOW_HZ, 0.28)
    out = np.zeros_like(seg)
    k = k0
    while True:
        t = a.offset_s + k * period
        i = int(t * sr) - i0
        if i >= len(seg):
            break
        if i >= 0:
            c = high if (k - a.downbeat) % 4 == 0 else low
            m = min(len(c), len(seg) - i)
            out[i:i + m] += c[:m, None]
        k += 1
    mix = seg + out
    peak = float(np.abs(mix).max())
    if peak > 0.98:
        mix = mix * (0.98 / peak)
    return mix.astype(np.float32), start


def explain_fix(current: int) -> list[str]:
    """What to do depending on where the listener hears the high click."""
    return [
        "Listen for the HIGH click. It marks what the software believes is beat 1 of each bar.",
        "Count along with the music (a bar is four beats; the kick usually hits every beat):",
        f"  - high click on your '1' (the start of the bar: chords change, a crash or a drop lands here): correct, "
        f"keep --downbeat {current} (or no override)",
        f"  - high click on your '2': use --downbeat {(current - 1) % 4}",
        f"  - high click on your '3': use --downbeat {(current - 2) % 4}",
        f"  - high click on your '4': use --downbeat {(current + 1) % 4}",
        "If the clicks drift out of time with the kick the tempo or grid is wrong, not just the downbeat.",
    ]
