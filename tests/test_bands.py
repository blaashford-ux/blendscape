# SPDX-License-Identifier: GPL-3.0-or-later
import numpy as np
import pytest

from blendscape.bands import bass_swap_gains, crossfade_gains, ramp, split3
from blendscape.model import EQCurves

SR = 44100


def test_bands_sum_to_flat_magnitude():
    imp = np.zeros((SR, 1), np.float32)
    imp[100] = 1.0
    lo, mid, hi = split3(imp, SR)
    total = np.abs(np.fft.rfft((lo + mid + hi)[:, 0]))
    freqs = np.fft.rfftfreq(SR, 1 / SR)
    band = (freqs > 30) & (freqs < 18000)
    assert np.all(np.abs(20 * np.log10(total[band])) < 0.25)  # within a quarter dB


def test_bands_separate_frequencies():
    t = np.arange(SR * 2) / SR
    tone = lambda f: np.sin(2 * np.pi * f * t)[:, None].astype(np.float32)  # noqa: E731
    rms = lambda x: float(np.sqrt(np.mean(x[SR // 2:] ** 2)))  # noqa: E731
    lo, mid, hi = split3(tone(60), SR)
    assert rms(lo) > 10 * rms(mid) and rms(lo) > 10 * rms(hi)
    lo, mid, hi = split3(tone(800), SR)
    assert rms(mid) > 10 * rms(lo) and rms(mid) > 10 * rms(hi)
    lo, mid, hi = split3(tone(8000), SR)
    assert rms(hi) > 10 * rms(lo) and rms(hi) > 10 * rms(mid)


def test_ramp_edges_and_midpoint():
    u = np.array([-1.0, 0.0, 5.0, 10.0, 12.0])
    r = ramp(u, 0.0, 10.0)
    assert r[0] == 0 and r[1] == 0 and r[3] == 1 and r[4] == 1
    assert r[2] == pytest.approx(0.5)


def test_bass_swap_gains_start_and_end_states():
    n = 32
    u = np.array([0.0, n / 2, float(n)])
    ga, gb = bass_swap_gains(u, n, EQCurves())
    # start: A fully there, B silent; end: A silent, B fully there
    for k in ("lo", "mid", "hi"):
        assert ga[k][0] == pytest.approx(1.0) and gb[k][0] == pytest.approx(0.0)
        assert ga[k][2] == pytest.approx(0.0) and gb[k][2] == pytest.approx(1.0)
    # bass swaps at the centre: halfway both are 0.5
    assert ga["lo"][1] == pytest.approx(0.5) and gb["lo"][1] == pytest.approx(0.5)


def test_bass_is_never_doubled_during_the_swap():
    n = 32
    u = np.linspace(0, n, 2000)
    ga, gb = bass_swap_gains(u, n, EQCurves())
    assert np.all(ga["lo"] + gb["lo"] <= 1.0 + 1e-9)  # complementary raised cosines sum to exactly 1
    # and the swap is quick: bass of A is full until 2 beats before the centre
    assert np.all(ga["lo"][u < n / 2 - 2.0] == 1.0)
    assert np.all(gb["lo"][u > n / 2 + 2.0] == 1.0)


def test_incoming_highs_arrive_before_the_bass_swap_and_outgoing_highs_leave_after():
    n = 32
    ga, gb = bass_swap_gains(np.array([0.3 * n]), n, EQCurves())
    assert gb["hi"][0] > 0.5 and gb["lo"][0] == 0.0
    ga, gb = bass_swap_gains(np.array([0.7 * n]), n, EQCurves())
    assert ga["lo"][0] == 0.0 and ga["hi"][0] > 0.0


def test_crossfade_is_equal_power():
    u = np.linspace(0, 16, 100)
    ga, gb = crossfade_gains(u, 16)
    np.testing.assert_allclose(ga["lo"] ** 2 + gb["lo"] ** 2, 1.0, atol=1e-9)
    assert ga["mid"][0] == pytest.approx(1.0) and gb["mid"][-1] == pytest.approx(1.0)
