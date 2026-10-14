# SPDX-License-Identifier: GPL-3.0-or-later
import numpy as np
import pytest
from scipy.signal import find_peaks

from blendscape.analysis import TrackAnalysis
from blendscape.check import HIGH_HZ, LOW_HZ, click_check, default_start_s, explain_fix

SR = 44100


def analysis(bpm=120.0, offset=0.3, db=1, sections=None):
    return TrackAnalysis(path="x.wav", sample_rate=SR, duration_s=100.0, bpm=bpm, offset_s=offset, downbeat=db,
                         downbeat_confidence=0.9, sections=sections or [])


def clicks_only(a, **kw):
    """Run the click renderer over silence, so the output is just the metronome."""
    audio = np.zeros((SR * 60, 2), np.float32)
    mix, start = click_check(a, audio, **kw)
    return mix, start


def detect(mix, a):
    """Per click: (time, is_high), judged by which of the two click pitches the burst contains."""
    pad = 100  # a click can sit on the very first sample
    mix = np.concatenate([np.zeros((pad, mix.shape[1]), mix.dtype), mix])
    env = np.abs(mix[:, 0])
    peaks, _ = find_peaks(env, height=0.1, distance=int(0.2 * SR))
    out = []
    for p in peaks:
        seg = mix[p - 40: p + 900, 0]
        spec = np.abs(np.fft.rfft(seg * np.hanning(len(seg)), 8192))
        f = np.fft.rfftfreq(8192, 1 / SR)
        hi = spec[np.argmin(np.abs(f - HIGH_HZ))]
        lo = spec[np.argmin(np.abs(f - LOW_HZ))]
        out.append(((p - pad) / SR, hi > lo))
    return out


def test_one_click_per_beat_with_the_high_one_on_the_bar_line():
    a = analysis(bpm=120.0, offset=0.3, db=1)
    mix, start = clicks_only(a, start_s=2.0, seconds=10.0)
    found = detect(mix, a)
    period = 0.5
    assert len(found) == pytest.approx(10 / period, abs=1)
    gaps = np.diff([t for t, _ in found])
    assert np.allclose(gaps, period, atol=0.002)
    highs = [i for i, (_, hi) in enumerate(found) if hi]
    assert highs == list(range(0, len(found), 4))  # every fourth click, starting with the first
    # the first click is on a grid beat that is a bar line (beat index = 1 mod 4)
    k = round((start - a.offset_s) / period)
    assert (k - a.downbeat) % 4 == 0


def test_start_is_snapped_back_to_a_bar_line():
    a = analysis(bpm=100.0, offset=0.1, db=2)
    _, start = clicks_only(a, start_s=7.3, seconds=5.0)
    period = 0.6
    k = (start - 0.1) / period
    assert k == pytest.approx(round(k), abs=1e-6) and (round(k) - 2) % 4 == 0
    assert 7.3 - 4 * period <= start <= 7.3


def test_changing_the_downbeat_moves_the_high_click():
    a = analysis(db=0)
    b = analysis(db=2)
    ma, sa = clicks_only(a, start_s=2.0, seconds=8.0)
    mb, sb = clicks_only(b, start_s=2.0, seconds=8.0)
    ha = [sa + t for t, hi in detect(ma, a) if hi]  # absolute times in the file
    hb = [sb + t for t, hi in detect(mb, b) if hi]
    d = (ha[0] - hb[0]) % 2.0  # a bar is 2 s at 120 BPM; db 0 vs 2 is two beats = half a bar apart
    assert d == pytest.approx(1.0, abs=0.01)


def test_music_is_kept_and_output_never_clips():
    a = analysis()
    music = np.full((SR * 20, 2), 0.95, np.float32)
    mix, _ = click_check(a, music, start_s=1.0, seconds=5.0)
    assert np.abs(mix).max() <= 0.981
    assert mix.shape[1] == 2 and len(mix) == 5 * SR


def test_default_start_is_the_first_body_bar():
    secs = [{"label": "intro", "start_bar": 0, "end_bar": 8}, {"label": "body", "start_bar": 8, "end_bar": 24}]
    a = analysis(bpm=120.0, offset=0.25, db=1, sections=secs)
    assert default_start_s(a) == pytest.approx(0.25 + (1 + 32) * 0.5)
    assert default_start_s(analysis(db=2)) == pytest.approx(0.3 + 2 * 0.5)  # no sections: first bar line


def test_instructions_give_the_right_fix_for_each_wrong_beat():
    text = "\n".join(explain_fix(2))
    assert "keep --downbeat 2" in text
    assert "'2': use --downbeat 1" in text and "'3': use --downbeat 0" in text and "'4': use --downbeat 3" in text
