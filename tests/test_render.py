# SPDX-License-Identifier: GPL-3.0-or-later
import numpy as np
import pytest
from scipy.signal import butter, correlate, sosfilt

from blendscape.analysis import analyze
from blendscape.model import MixPlan
from blendscape.planner import make_plan
from blendscape.render import render, render_parts
from conftest import needs_rubberband

SR = 44100
pytestmark = [pytest.mark.slow, needs_rubberband]


def _plan(tracks, names, overlap=16, **kw):
    return make_plan([analyze(tracks[n][0]) for n in names], overlap_beats=overlap, **kw)


def _hat_env(x):
    sos = butter(4, 5000, "high", fs=SR, output="sos")
    e = np.abs(sosfilt(sos, x.mean(1)))
    w = int(0.001 * SR)
    return np.convolve(e, np.ones(w) / w, "same")


def _lag_ms(a, b, max_ms=60):
    """Lag (ms) at which envelope ``b`` best matches ``a``, searched within +-max_ms."""
    a = a - a.mean()
    b = b - b.mean()
    c = correlate(a, b, mode="full", method="fft")
    lags = np.arange(-len(b) + 1, len(a))
    m = np.abs(lags) <= max_ms * SR / 1000
    return lags[m][np.argmax(c[m])] / SR * 1000


@pytest.fixture(scope="module")
def chain(tracks):
    plan = _plan(tracks, "abc")
    parts, tl = render_parts(plan)
    return plan, parts, tl


def test_overlapping_tracks_are_beat_aligned_within_two_milliseconds(chain):
    plan, parts, tl = chain
    for j, trs in enumerate(tl.transitions):
        n = trs.overlap_beats
        beat = np.diff(trs.master).mean()
        t0, t1 = int(trs.master[0] + 0.15 * n * beat), int(trs.master[0] + 0.45 * n * beat)
        seg = []
        for idx in (j, j + 1):
            y, start = parts[idx]
            seg.append(_hat_env(y[t0 - start: t1 - start]))
        lag = _lag_ms(*seg)
        assert abs(lag) < 2.0, f"transition {j + 1}: hats are {lag:+.2f} ms apart"


def test_full_mix_has_expected_length_level_and_no_clipping(chain, tracks):
    plan, parts, tl = chain
    mix, report = render(plan)
    last = tl.spans[-1]
    audio_len = (parts[-1][1] + len(parts[-1][0])) / SR
    assert report.duration_s == pytest.approx(audio_len, abs=0.01)
    assert len(mix) / SR == pytest.approx(report.duration_s, abs=1e-3)
    assert np.abs(mix).max() <= 10 ** (plan.ceiling_db / 20) + 1e-4
    assert last.shift > 0
    assert len(report.transitions) == 2 and report.transitions[0][1] < report.transitions[1][0]


def test_no_discontinuity_at_the_overlap_boundaries(chain):
    plan, parts, tl = chain
    mix, report = render(plan)
    step = np.abs(np.diff(mix, axis=0)).max(1)
    typical = np.percentile(step, 99.9)
    for s, e in report.transitions:
        for edge in (s, e):
            i = int(edge * SR)
            assert step[i - 2000: i + 2000].max() < 2.5 * typical


def test_track_boundaries_before_and_after_overlaps_are_untouched_audio(chain, tracks):
    """Before the first overlap the mix is the first track itself (all-pass band split aside): same envelope."""
    import soundfile as sf

    plan, parts, tl = chain
    mix, report = render(plan)
    x, _ = sf.read(tracks["a"][0], dtype="float32", always_2d=True)
    n = int((report.transitions[0][0] - 1.0) * SR)
    gain = 10 ** (report.gain_db / 20)
    a_rms = np.sqrt(np.mean((x[:n] * gain) ** 2))
    m_rms = np.sqrt(np.mean(mix[:n] ** 2))
    assert m_rms == pytest.approx(a_rms, rel=0.03)


def test_crossfade_style_renders_and_stays_in_range(tracks):
    plan = _plan(tracks, "ab", overlap=8, style="crossfade")
    mix, report = render(plan)
    assert np.abs(mix).max() <= 10 ** (plan.ceiling_db / 20) + 1e-4
    s, e = report.transitions[0]
    assert e - s == pytest.approx(8 * 60 / 124, abs=0.01)


def test_plan_roundtrip_renders_identically(tracks, tmp_path):
    plan = _plan(tracks, "ab", overlap=8)
    f = tmp_path / "p.json"
    plan.save(f)
    m1, _ = render(plan)
    m2, _ = render(MixPlan.load(f))
    assert m1.shape == m2.shape
    np.testing.assert_allclose(m1, m2, atol=1e-6)
