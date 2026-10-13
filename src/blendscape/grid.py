# SPDX-License-Identifier: GPL-3.0-or-later
"""Beat-grid arithmetic. A grid is steady-tempo: beat k sits at ``offset_s + k * 60 / bpm`` seconds."""
from __future__ import annotations

import math
from dataclasses import dataclass

BEATS_PER_BAR = 4


@dataclass(frozen=True)
class BeatGrid:
    offset_s: float  # time of beat index 0 in the source file
    bpm: float
    downbeat: int  # beat index in 0..3 that starts a bar (bar 1 = beats downbeat, downbeat+4, ...)

    @property
    def period_s(self) -> float:
        return 60.0 / self.bpm

    def beat_time(self, k: float) -> float:
        return self.offset_s + k * self.period_s

    def beat_sample(self, k: float, sr: int) -> float:
        return self.beat_time(k) * sr

    def n_beats(self, duration_s: float) -> int:
        """Number of grid beats that start strictly inside a file of this duration (valid indices 0..n-1)."""
        if duration_s <= self.offset_s:
            return 0
        return math.ceil((duration_s - self.offset_s) / self.period_s - 1e-9)

    def is_downbeat(self, k: int) -> bool:
        return (k - self.downbeat) % BEATS_PER_BAR == 0

    def bar_start(self, bar: int) -> int:
        """Beat index at which bar number ``bar`` (0-based) starts."""
        return self.downbeat + BEATS_PER_BAR * bar

    def phrase_boundaries(self, duration_s: float, phrase_bars: int = 8) -> list[int]:
        """Beat indices of phrase starts (every ``phrase_bars`` bars from the first downbeat) inside the file."""
        step = BEATS_PER_BAR * phrase_bars
        return list(range(self.downbeat, self.n_beats(duration_s), step))

    def nearest_beat(self, t_s: float) -> int:
        return round((t_s - self.offset_s) / self.period_s)

    def clock(self, k: int) -> str:
        """Human readable position of beat ``k``: ``m:ss.s``."""
        t = self.beat_time(k)
        return f"{int(t // 60)}:{t % 60:04.1f}"
