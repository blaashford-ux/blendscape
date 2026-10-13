# SPDX-License-Identifier: GPL-3.0-or-later
import numpy as np
import pytest

from blendscape.stretch import _keyframes, warp
from conftest import needs_rubberband
from synth import SR, kick


def click_track(bpm, n_beats, offset_s=0.37):
    beat = 60 / bpm * SR
    x = np.zeros((int((offset_s + n_beats * 60 / bpm + 2) * SR), 2), np.float32)
    k = kick().astype(np.float32)
    pos = offset_s * SR + beat * np.arange(n_beats)
    for p in pos:
        j = int(round(p))
        x[j: j + len(k)] += k[:, None]
    return x, pos


def onsets(y, bpm, thresh=0.2):
    e = np.abs(y.mean(1))
    thr = thresh * e.max()
    out, i, gap = [], 0, int(0.6 * 60 / bpm * SR)
    while i < len(e):
        if e[i] > thr:
            out.append(i)
            i += gap
        else:
            i += 1
    return np.array(out)


def test_keyframes_are_monotonic_and_handle_unity_ends():
    keys, tos, d0 = _keyframes(1000, [100.2, 300.0, 500.0], [5100.0, 5290.0, 5470.0])
    assert keys == [0, 100, 300, 500, 1000]
    assert d0 == 5000
    assert tos == [0, 100, 290, 470, 970]
    assert all(b > a for a, b in zip(keys, keys[1:], strict=False))
    assert all(b > a for a, b in zip(tos, tos[1:], strict=False))


def test_keyframes_drop_anchors_beyond_the_audio_and_non_monotonic_ones():
    keys, tos, d0 = _keyframes(400, [0, 100, 100, 600], [10, 110, 120, 700])
    assert keys == [0, 100, 400]  # duplicate source and out-of-range anchors are dropped
    assert d0 == 10


@needs_rubberband
@pytest.mark.parametrize("target_bpm", [128.0, 120.0])
def test_constant_stretch_places_every_beat_exactly(target_bpm):
    bpm, n = 124.0, 80
    x, pos = click_track(bpm, n)
    r = bpm / target_bpm
    abs_off = 30_000  # arbitrary absolute master position
    dst = pos * r + abs_off
    y, start = warp(x, SR, pos, dst)
    o = start + onsets(y, target_bpm)[: n - 2]
    err = o - dst[: len(o)]
    assert abs(err.mean()) < 90  # < 2 ms: constant detector latency
    assert err.std() < 25  # < 0.6 ms jitter
    assert len(y) / len(x) == pytest.approx(r, rel=0.02)


@needs_rubberband
def test_tempo_ramp_keeps_beats_on_the_target_map():
    bpm, n = 124.0, 100
    x, pos = click_track(bpm, n)
    ratios = np.ones(n - 1)
    ratios[20:52] = np.linspace(1, 124 / 128, 32)
    ratios[52:] = 124 / 128
    dst = np.concatenate([[pos[0]], pos[0] + np.cumsum(np.diff(pos) * ratios)])
    y, start = warp(x, SR, pos, dst)
    o = start + onsets(y, 126.0)[: n - 4]
    err = o - dst[: len(o)]
    assert err.std() < 30 and abs(err.mean()) < 90


@needs_rubberband
def test_region_starting_mid_file_lands_at_the_requested_master_time():
    x, pos = click_track(124.0, 40)
    s0 = int(round(pos[10]))
    region = x[s0:]
    local = pos[10:30] - s0
    dst = 200_000 + (pos[10:30] - pos[10]) * 0.97
    y, start = warp(region, SR, local, dst)
    assert abs(start - 200_000) <= 1  # first anchor is at the region start
    first_click = start + onsets(y, 128.0)[0]
    assert abs(first_click - dst[0]) < 90
