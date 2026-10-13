# SPDX-License-Identifier: GPL-3.0-or-later
import pytest

from blendscape.analysis import TrackAnalysis
from blendscape.model import PlanError, validate
from blendscape.planner import make_plan


def _a(name, bpm, dur=200.0, offset=0.3, db=0):
    return TrackAnalysis(path=f"/m/{name}.wav", sample_rate=44100, duration_s=dur, bpm=bpm, offset_s=offset,
                         downbeat=db, downbeat_confidence=1.5)


def test_default_plan_is_valid_and_uses_phrase_boundaries():
    plan = make_plan([_a("a", 124, db=1), _a("b", 128, db=3), _a("c", 126, db=0)], overlap_beats=32)
    assert validate(plan) == []
    for i, tr in enumerate(plan.transitions):
        a = plan.tracks[i]
        assert (tr.out_beat - a.downbeat) % 32 == 0  # on an 8-bar phrase boundary
        assert tr.out_beat + tr.overlap_beats <= a.n_beats - 1
    # incoming track enters at its first downbeat
    assert [t.in_beat for t in plan.transitions] == [3, 0]


def test_outgoing_track_leaves_as_late_as_possible():
    plan = make_plan([_a("a", 120, dur=200.0), _a("b", 120)], overlap_beats=32)
    a = plan.tracks[0]
    tr = plan.transitions[0]
    # the next phrase boundary would no longer fit the overlap
    assert tr.out_beat + 32 + 32 > a.n_beats - 1


def test_middle_track_waits_until_it_has_finished_entering():
    plan = make_plan([_a("a", 120), _a("b", 120, db=2), _a("c", 120)], overlap_beats=64)
    assert plan.transitions[1].out_beat >= plan.transitions[0].in_beat + 64


def test_phrase_length_falls_back_for_short_tracks():
    plan = make_plan([_a("a", 120, dur=40.0), _a("b", 120)], overlap_beats=16, phrase_bars=8)
    assert validate(plan) == []
    assert plan.tracks[0].grid.is_downbeat(plan.transitions[0].out_beat)  # bar boundary fallback


def test_track_too_short_for_the_overlap():
    with pytest.raises(PlanError, match="too short"):
        make_plan([_a("a", 120, dur=12.0), _a("b", 120)], overlap_beats=64)


def test_ids_are_unique_even_for_the_same_file_twice():
    plan = make_plan([_a("same", 120), _a("same", 120)])
    assert plan.tracks[0].id != plan.tracks[1].id
