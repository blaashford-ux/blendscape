# SPDX-License-Identifier: GPL-3.0-or-later
import pytest

from blendscape.grid import BeatGrid


def test_beat_time_and_period():
    g = BeatGrid(offset_s=0.5, bpm=120.0, downbeat=0)
    assert g.period_s == pytest.approx(0.5)
    assert g.beat_time(0) == pytest.approx(0.5)
    assert g.beat_time(8) == pytest.approx(4.5)
    assert g.beat_sample(8, 44100) == pytest.approx(4.5 * 44100)


def test_n_beats_counts_beats_starting_inside_the_file():
    g = BeatGrid(0.5, 120.0, 0)
    assert g.n_beats(0.4) == 0
    assert g.n_beats(0.5) == 0  # beat 0 would start exactly at the end: no audio in it
    assert g.n_beats(0.99) == 1
    assert g.n_beats(10.0) == 19  # beats at 0.5, 1.0, ..., 9.5 start before 10.0


def test_downbeat_and_bars():
    g = BeatGrid(0.0, 120.0, downbeat=2)
    assert [k for k in range(12) if g.is_downbeat(k)] == [2, 6, 10]
    assert g.bar_start(0) == 2
    assert g.bar_start(3) == 14


def test_phrase_boundaries_start_at_first_downbeat():
    g = BeatGrid(0.0, 120.0, downbeat=1)
    # 120 BPM -> 2 beats/s; 80 s -> 160 beats; 8-bar phrase = 32 beats
    assert g.phrase_boundaries(80.0, 8) == [1, 33, 65, 97, 129]
    assert g.phrase_boundaries(80.0, 4) == [1, 17, 33, 49, 65, 81, 97, 113, 129, 145]


def test_nearest_beat():
    g = BeatGrid(0.25, 60.0, 0)
    assert g.nearest_beat(3.3) == 3
    assert g.nearest_beat(3.8) == 4


def test_clock_format():
    g = BeatGrid(0.0, 60.0, 0)
    assert g.clock(75) == "1:15.0"
