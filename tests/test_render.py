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
    a_rms = np.sqrt(np.mean(x[:n] ** 2))
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


def test_limiter_only_touches_peaks_not_the_whole_mix(tracks):
    """The old behaviour scaled the entire mix to fit the ceiling. The summed mix peaks about +0.4 dBFS, so a -3 dB
    ceiling needs ~3.4 dB of reduction at the peaks; that would have dragged the kick-less intro down by 3.4 dB
    too. With a limiter the intro (whose few pad accents only just touch the ceiling) stays within ~0.7 dB."""
    import soundfile as sf

    plan = _plan(tracks, "ab", overlap=8, mix_points="latest")  # pins the overlap to a loud spot
    plan.ceiling_db = -3.0
    mix, report = render(plan)
    assert report.max_reduction_db > 3.0 and 0 < report.limited_fraction < 0.95
    assert report.peak_after_db <= -3.0 + 0.05
    assert np.abs(mix).max() <= 10 ** (-3.0 / 20) + 1e-4
    x, _ = sf.read(tracks["a"][0], dtype="float32", always_2d=True)
    n = int(10 * SR)  # inside A's kick-less 8-bar intro (about 15 s)
    ratio = np.sqrt(np.mean(mix[:n] ** 2)) / np.sqrt(np.mean(x[:n] ** 2))
    assert ratio > 10 ** (-0.7 / 20)  # whole-mix scaling would give 0.68


def test_this_mix_would_clip_without_the_limiter_and_does_not_with_it(tracks):
    plan = _plan(tracks, "ab", overlap=8, mix_points="latest")  # pins the overlap to a loud spot
    plan.limiter.enabled = False
    mix, report = render(plan)
    assert report.clipped and report.peak_before_db > 0  # two loud tracks summed in the overlap go over full scale
    assert np.abs(mix).max() == 1.0  # clipped hard on the way out
    plan.limiter.enabled = True
    mix, report = render(plan)
    assert not report.clipped and report.peak_after_db <= plan.ceiling_db + 0.05
    assert report.max_reduction_db > 0.5


def test_preview_covers_the_overlap_plus_the_requested_lead_and_tail(tracks):
    from blendscape.render import render_preview

    plan = _plan(tracks, "ab", overlap=16, mix_points="latest")
    tr = plan.transitions[0]
    audio, sr, rep = render_preview(plan, 0, lead_s=3.0, tail_s=5.0)
    overlap_s = tr.overlap_beats * 60 / plan.tracks[0].bpm
    assert sr == SR and audio.shape[1] == 2
    assert len(audio) / sr == pytest.approx(3.0 + overlap_s + 5.0, abs=0.6)  # the glide makes it a little shorter
    s, e = rep.transitions[0]
    assert s == pytest.approx(3.0, abs=0.01) and e - s == pytest.approx(overlap_s, abs=0.7)
    assert np.abs(audio).max() <= 10 ** (plan.ceiling_db / 20) + 1e-4
    # before the overlap it is the outgoing track on its own, after it the incoming one on its own
    assert np.sqrt(np.mean(audio[: int(2.0 * sr)] ** 2)) > 0.01
    assert np.sqrt(np.mean(audio[-int(2.0 * sr):] ** 2)) > 0.01
    with pytest.raises(ValueError):
        render_preview(plan, 5)
