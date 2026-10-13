# SPDX-License-Identifier: GPL-3.0-or-later
import numpy as np
import pytest

from blendscape.model import MixPlan, TrackEntry, Transition
from blendscape.timeline import build_timeline

SR = 44100


def _t(i, bpm, offset=0.3, dur=240.0, db=0):
    return TrackEntry(id=f"t{i}", path=f"/x/{i}.wav", bpm=bpm, offset_s=offset, downbeat=db, duration_s=dur)


def _plan(bpms=(120.0, 128.0, 126.0), overlap=32, style="bass_swap"):
    tracks = [_t(i, b, offset=0.3 + 0.1 * i, db=i % 4) for i, b in enumerate(bpms)]
    trans = []
    free = 0
    for i in range(len(tracks) - 1):
        trans.append(Transition(tracks[i].id, tracks[i + 1].id, out_beat=free + 96, in_beat=tracks[i + 1].downbeat,
                                overlap_beats=overlap, style=style))
        free = trans[-1].in_beat + overlap
    return MixPlan(tracks, trans, sample_rate=SR)


def test_overlap_beats_of_both_tracks_coincide_on_the_master_clock():
    plan = _plan()
    tl = build_timeline(plan)
    for i, trs in enumerate(tl.transitions):
        a, b = tl.spans[i], tl.spans[i + 1]
        # last N+1 anchors of the outgoing track, all anchors of the incoming one's entrance
        n = trs.overlap_beats + 1
        np.testing.assert_allclose(a.anchors_dst[-n:], trs.master)
        np.testing.assert_allclose(b.anchors_dst[:n], trs.master)


def test_master_period_glides_from_outgoing_to_incoming_tempo():
    plan = _plan(bpms=(120.0, 128.0))
    trs = build_timeline(plan).transitions[0]
    periods = np.diff(trs.master)
    pa, pb = 60 / 120 * SR, 60 / 128 * SR
    assert periods[0] == pytest.approx(pa + (pb - pa) * 0.5 / 32)
    assert periods[-1] == pytest.approx(pb - (pb - pa) * 0.5 / 32)
    assert np.all(np.diff(periods) < 0)  # speeding up monotonically
    assert periods.mean() == pytest.approx((pa + pb) / 2)


def test_overlap_starts_where_the_outgoing_track_is_at_that_beat():
    plan = _plan(bpms=(120.0, 128.0))
    tl = build_timeline(plan)
    tr = plan.transitions[0]
    assert tl.transitions[0].master[0] == pytest.approx(plan.tracks[0].grid.beat_sample(tr.out_beat, SR))


def test_incoming_track_plays_natively_after_the_overlap():
    plan = _plan(bpms=(120.0, 128.0))
    tl = build_timeline(plan)
    tr = plan.transitions[0]
    b = plan.tracks[1]
    end_src = b.grid.beat_sample(tr.in_beat + tr.overlap_beats, SR)
    assert end_src + tl.spans[1].shift == pytest.approx(tl.transitions[0].master[-1])
    # one native beat later in the source is exactly one native beat later on the master clock
    assert (end_src + SR * b.grid.period_s) + tl.spans[1].shift == pytest.approx(
        tl.transitions[0].master[-1] + SR * b.grid.period_s)


def test_chain_second_transition_is_built_on_the_shifted_middle_track():
    plan = _plan()
    tl = build_timeline(plan)
    t2 = plan.transitions[1]
    mid = plan.tracks[1]
    expect = mid.grid.beat_sample(t2.out_beat, SR) + tl.spans[1].shift
    assert tl.transitions[1].master[0] == pytest.approx(expect)
    assert tl.transitions[1].master[0] > tl.transitions[0].master[-1]  # after the first overlap
    # the middle track carries both overlaps' anchors, in increasing order
    s = tl.spans[1]
    assert np.all(np.diff(s.anchors_src) > 0) and np.all(np.diff(s.anchors_dst) > 0)
    assert len(s.anchors_src) == 2 * 33


def test_region_flags():
    tl = build_timeline(_plan())
    assert [s.to_end_of_file for s in tl.spans] == [False, False, True]
    assert tl.spans[0].src_start == 0.0


def test_crossfade_has_no_anchors_and_aligns_native_beats_at_the_cut():
    plan = _plan(bpms=(120.0, 128.0), style="crossfade")
    tl = build_timeline(plan)
    assert all(len(s.anchors_src) == 0 for s in tl.spans)
    tr = plan.transitions[0]
    m0 = tl.transitions[0].master[0]
    assert plan.tracks[1].grid.beat_sample(tr.in_beat, SR) + tl.spans[1].shift == pytest.approx(m0)
    assert len(tl.transitions[0].master) == 2


def test_invalid_plans_are_rejected_before_building():
    plan = _plan()
    plan.transitions[1].out_beat = 5
    with pytest.raises(ValueError):
        build_timeline(plan)
