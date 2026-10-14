# SPDX-License-Identifier: GPL-3.0-or-later
import numpy as np
import pytest
import soundfile as sf

from blendscape import downbeat as D


def _truth_estimates(rich_set, shift_beats=0):
    out = []
    for t in rich_set:
        y, sr = sf.read(t.path, dtype="float32", always_2d=True)
        period = 60 / t.bpm
        out.append((t, D.estimate_downbeat(y.mean(1), sr, t.offset_s + shift_beats * period, period)))
    return out


@pytest.fixture(scope="module")
def estimates(rich_set):
    return _truth_estimates(rich_set)


# ---------- the whole estimator on synthetic music with a known bar line ----------
def test_finds_the_bar_line_on_every_style_that_has_a_cue(estimates):
    cued = [(t, e) for t, e in estimates if t.style != "flat"]
    wrong = [(t.style, t.downbeat, e.phase) for t, e in cued if e.phase != t.downbeat]
    assert not wrong, f"wrong bar line: {wrong}"


def test_says_so_when_there_is_nothing_to_go_on(estimates):
    flat = [e for t, e in estimates if t.style == "flat"]
    assert flat and all(e.confidence < 0.5 for e in flat)  # no cue exists, so it must not claim certainty


def test_high_confidence_means_right(estimates):
    sure = [(t, e) for t, e in estimates if e.confidence >= 0.8]
    assert len(sure) >= 4
    assert all(e.phase == t.downbeat for t, e in sure)


def test_probabilities_are_a_distribution_and_confidence_is_the_winner(estimates):
    for _t, e in estimates:
        assert sum(e.probs) == pytest.approx(1.0) and len(e.probs) == 4
        assert e.confidence == pytest.approx(max(e.probs)) and e.probs[e.phase] == e.confidence
        assert 0.25 - 1e-9 <= e.confidence <= 1.0
        assert set(e.cues) == set(D.WEIGHTS)


def test_moving_the_grid_by_a_beat_moves_the_answer_by_a_beat(rich_set):
    shifted = _truth_estimates(rich_set[:6:2], shift_beats=1)  # one track from each of three styles
    for t, e in shifted:
        assert e.phase == (t.downbeat - 1) % 4, t.style


def test_too_short_to_judge():
    y = np.random.default_rng(0).standard_normal(44100 * 5).astype(np.float32)
    e = D.estimate_downbeat(y, 44100, 0.1, 0.5)
    assert e.probs == [0.25] * 4 and e.confidence == 0.25


# ---------- individual cues ----------
def _pattern(n, accent_phase, boost=3.0, noise=0.3, seed=0):
    rng = np.random.default_rng(seed)
    a = 1.0 + noise * rng.standard_normal(n)
    a[accent_phase::4] += boost
    return np.maximum(a, 0)


@pytest.mark.parametrize("p", range(4))
def test_accent_cue_points_at_the_strong_beat(p):
    s = D._accent(_pattern(160, p))
    assert int(np.argmax(s)) == p and s[p] > 5


def test_accent_cue_is_quiet_on_noise():
    s = D._accent(np.random.default_rng(1).random(160))
    assert np.abs(s).max() < D.NOISE_T * 2


@pytest.mark.parametrize("p", range(4))
def test_backbeat_cue_knows_parity_but_not_which_of_two(p):
    # claps on the beats after the bar line and after beat 3: beats p+1 and p+3
    a = np.full(160, 0.2)
    k = np.arange(160) % 4
    a[(k - p) % 4 == 1] = 1.0
    a[(k - p) % 4 == 3] = 1.0
    s = D._backbeat(a + np.random.default_rng(2).random(160) * 0.05)
    assert s[p] > 5 and s[(p + 2) % 4] > 5  # p and p+2 are indistinguishable by this cue alone
    assert s[(p + 1) % 4] < -5 and s[(p + 3) % 4] < -5


def test_boundary_cue_follows_where_the_arrangement_changes():
    n = 128
    feat = np.zeros((n, 3))
    for start, level in ((16 + 2, 1.0), (48 + 2, 0.0), (80 + 2, 1.0)):  # three changes, all on beat phase 2
        feat[start:, :] = level
    s = D._boundary(feat)
    assert int(np.argmax(s)) == 2 and s[2] > 1.5
    assert np.all(D._boundary(np.zeros((n, 3))) == 0)  # nothing changes: no vote


def test_impact_cue_follows_the_loudest_hits():
    a = np.random.default_rng(3).random(160) * 0.2
    a[1::32] = 5.0  # a crash every 8 bars on phase 1
    s = D._impact(a)
    assert int(np.argmax(s)) == 1 and s[1] > 2
    assert np.all(D._impact(np.ones(3)) == 0)  # too little to go on


def test_vote_abstains_on_noise_shrinks_the_rest_and_caps_it():
    v = D._vote(np.array([0.5, -1.4, 3.0, -9.0, 100.0]))
    assert v[0] == 0 and v[1] == 0
    assert v[2] == pytest.approx(3.0 - D.NOISE_T)
    assert v[3] == pytest.approx(-(9.0 - D.NOISE_T) if 9.0 - D.NOISE_T < D.CLIP else -D.CLIP)
    assert v[4] == D.CLIP


def test_local_normalisation_stops_loud_sections_dominating():
    a = np.concatenate([np.full(80, 10.0), np.full(80, 0.1)])
    n = D._local_norm(a)
    assert n[40] == pytest.approx(n[120], rel=0.05)


def test_change_at_beats_peaks_where_the_harmony_changes():
    c = np.zeros((48, 12))
    c[:24, 0] = 1.0
    c[24:, 7] = 1.0
    ch = D._change_at_beats(c)
    assert int(np.argmax(ch)) == 24 and ch[24] > 0.9 and ch[10] < 1e-6
