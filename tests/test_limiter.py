# SPDX-License-Identifier: GPL-3.0-or-later
import time

import numpy as np
import pytest

from blendscape.limiter import apply_limiter, gain_curve, peak_envelope

SR = 44100
CEIL_DB = -1.0
CEIL = 10 ** (CEIL_DB / 20)


def tone(freq, seconds, amp, phase=0.0):
    t = np.arange(int(seconds * SR)) / SR
    return (amp * np.sin(2 * np.pi * freq * t + phase)).astype(np.float32)


def stereo(left, right=None):
    return np.stack([left, left if right is None else right], axis=1)


def true_peak(x):
    from scipy.signal import resample_poly

    return float(np.abs(resample_poly(x.astype(np.float64), 8, 1, axis=0)).max())


def burst(amp=2.0, at=1.0, total=3.0, width=0.05):
    """Quiet music-ish bed with one hot burst."""
    x = tone(220, total, 0.3)
    i, w = int(at * SR), int(width * SR)
    x[i: i + w] = tone(220, width, amp)[:w]
    return stereo(x)


def test_quiet_audio_passes_through_untouched():
    x = stereo(tone(440, 1.0, 0.5))
    y, rep = apply_limiter(x, SR, CEIL_DB)
    assert y is x or np.array_equal(y, x)
    assert rep.max_reduction_db == 0 and rep.limited_fraction == 0


def test_sample_peaks_never_exceed_the_ceiling():
    x = burst(amp=3.0)
    y, rep = apply_limiter(x, SR, CEIL_DB, true_peak=False)
    assert np.abs(y).max() <= CEIL + 1e-6
    assert rep.max_reduction_db == pytest.approx(20 * np.log10(3.0 / CEIL), abs=0.2)


def test_reported_reduction_matches_the_overshoot():
    x = burst(amp=2.0)  # needs gain CEIL/2 -> -7.02 dB
    _, rep = apply_limiter(x, SR, CEIL_DB, true_peak=False)
    assert rep.max_reduction_db == pytest.approx(7.02, abs=0.25)
    assert rep.peak_before_db == pytest.approx(6.02, abs=0.1)
    assert rep.peak_after_db <= CEIL_DB + 0.01


def test_true_peak_limiting_catches_inter_sample_overs():
    # sine at fs/4 with a 45 degree phase: every sample is 0.707 * amp, but the waveform reaches amp
    t = np.arange(SR) / SR
    x = stereo((1.3 * np.sin(2 * np.pi * (SR / 4) * t + np.pi / 4)).astype(np.float32))
    assert np.abs(x).max() == pytest.approx(0.919, abs=0.01)  # sample peaks look fine (< 1.0 ...)
    assert true_peak(x) == pytest.approx(1.3, abs=0.02)  # ... but the reconstructed wave is 2.3 dB over full scale
    sample_only, _ = apply_limiter(x, SR, CEIL_DB, true_peak=False)
    assert true_peak(sample_only) > CEIL * 1.2  # a sample-peak limiter lets it through
    limited, rep = apply_limiter(x, SR, CEIL_DB, true_peak=True)
    assert true_peak(limited) <= CEIL * 1.01  # within 0.1 dB
    assert rep.peak_after_db <= CEIL_DB + 0.1


def test_gain_ramps_in_before_the_peak_and_is_one_well_before_it():
    x = burst(amp=2.0, at=1.0)
    peak = peak_envelope(x, CEIL, True)
    g = gain_curve(peak, SR, CEIL, lookahead_ms=3.0, release_ms=120.0)
    onset = int(1.0 * SR)
    a = int(0.003 * SR)
    assert g[onset - 3 * a] == pytest.approx(1.0, abs=1e-6)  # untouched before the look-ahead window
    assert g[onset - a // 2] < 0.99  # already moving by the time the burst starts
    assert g[onset + int(0.01 * SR)] == pytest.approx(CEIL / 2, abs=0.02)  # full reduction under the burst


def test_gain_changes_are_smooth():
    g = gain_curve(peak_envelope(burst(amp=4.0), CEIL, True), SR, CEIL)
    assert np.abs(np.diff(g)).max() < 0.01  # no clicks: a 12 dB attack is spread over ~265 samples


def test_release_follows_the_set_time_constant_and_recovers_fully():
    release_ms = 120.0
    x = burst(amp=2.0, at=1.0, width=0.05)
    g = gain_curve(peak_envelope(x, CEIL, True), SR, CEIL, release_ms=release_ms)
    end = int((1.0 + 0.05) * SR) + int(0.003 * SR)  # reduction is at its deepest until the burst has ended
    r0 = 1 - g[end]
    assert r0 > 0.4
    # one time constant later the remaining reduction is about 1/e of the original
    r1 = 1 - g[end + int(release_ms * 1e-3 * SR)]
    assert r1 / r0 == pytest.approx(np.exp(-1), abs=0.05)
    assert g[end + int(1.5 * SR)] > 0.9999  # and it's gone well within two seconds


def test_stereo_image_is_preserved():
    left = burst(amp=2.0)[:, 0]
    right = left * 0.5
    y, _ = apply_limiter(stereo(left, right), SR, CEIL_DB)
    nz = np.abs(y[:, 0]) > 1e-3
    np.testing.assert_allclose(y[nz, 1] / y[nz, 0], 0.5, atol=1e-4)


def test_untouched_material_stays_bit_exact_far_from_the_burst():
    x = burst(amp=2.0, at=1.0, total=6.0)
    y, _ = apply_limiter(x, SR, CEIL_DB)
    far = slice(int(0.0 * SR), int(0.9 * SR))
    np.testing.assert_array_equal(y[far], x[far])


def test_true_peak_envelope_is_chunk_size_independent():
    rng = np.random.default_rng(0)
    x = stereo((rng.standard_normal(SR * 3) * 0.5).astype(np.float32))
    whole = peak_envelope(x, CEIL, True, chunk=len(x))
    pieces = peak_envelope(x, CEIL, True, chunk=10_000)
    np.testing.assert_allclose(whole, pieces, atol=2e-3)
    assert np.all(whole >= np.abs(x).max(axis=1) - 1e-6)  # never below the sample peak


def test_long_mix_is_fast_enough():
    rng = np.random.default_rng(1)
    x = stereo((rng.standard_normal(SR * 120) * 0.4).astype(np.float32))
    t = time.perf_counter()
    y, rep = apply_limiter(x, SR, CEIL_DB)
    assert time.perf_counter() - t < 30
    assert rep.max_reduction_db > 0
    # BS.1770 true peak (4x) is held exactly. Wideband white noise is the worst case for a 4x meter, so the
    # real (8x+) peak can sit a fraction of a dB higher; band-limited programme material stays within ~0.05 dB.
    assert rep.peak_after_db <= CEIL_DB + 0.02
    assert true_peak(y) <= CEIL * 10 ** (0.5 / 20)


def test_band_limited_material_is_within_a_few_hundredths_of_a_db_at_any_oversampling():
    from scipy.signal import butter, sosfilt

    rng = np.random.default_rng(1)
    x = sosfilt(butter(4, 8000, fs=SR, output="sos"), rng.standard_normal(SR * 20) * 0.4).astype(np.float32)
    y, _ = apply_limiter(stereo(x), SR, CEIL_DB)
    assert true_peak(y) <= CEIL * 10 ** (0.1 / 20)
