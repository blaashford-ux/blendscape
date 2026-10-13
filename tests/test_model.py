# SPDX-License-Identifier: GPL-3.0-or-later
import copy
import json

import pytest

from blendscape.model import EQCurves, MixPlan, PlanError, TrackEntry, Transition, check, validate


def _track(i, bpm=120.0, dur=200.0, offset=0.0, db=0):
    return TrackEntry(id=f"t{i}", path=f"/x/t{i}.wav", bpm=bpm, offset_s=offset, downbeat=db, duration_s=dur)


def _plan(n=3, **tr):
    tracks = [_track(i) for i in range(n)]
    trans = []
    free = 0
    for i in range(n - 1):
        trans.append(Transition(f"t{i}", f"t{i + 1}", out_beat=free + 64, in_beat=0, overlap_beats=32, **tr))
        free = 0 + 32
    return MixPlan(tracks, trans)


def test_valid_plan_has_no_errors():
    assert validate(_plan()) == []
    check(_plan())


def test_roundtrip_json(tmp_path):
    plan = _plan()
    plan.transitions[0].eq = EQCurves(bass_swap_center=0.4, mid_in=(0.1, 0.5))
    f = tmp_path / "p.json"
    plan.save(f)
    again = MixPlan.load(f)
    assert again.transitions[0].eq.mid_in == (0.1, 0.5)
    assert json.loads(json.dumps(again.to_dict())) == json.loads(json.dumps(plan.to_dict()))
    assert [t.id for t in again.tracks] == ["t0", "t1", "t2"]


def test_relative_paths_resolve_against_plan_file(tmp_path):
    plan = _plan(2)
    for t in plan.tracks:
        t.path = f"audio/{t.id}.wav"
    f = tmp_path / "plans" / "p.json"
    f.parent.mkdir()
    plan.save(f)
    again = MixPlan.load(f)
    assert again.tracks[0].path == str(f.parent / "audio" / "t0.wav")


def test_wrong_version_and_bad_json(tmp_path):
    d = _plan().to_dict()
    d["version"] = 99
    with pytest.raises(PlanError, match="version"):
        MixPlan.from_dict(d)
    f = tmp_path / "bad.json"
    f.write_text("{nope")
    with pytest.raises(PlanError, match="invalid JSON"):
        MixPlan.load(f)
    with pytest.raises(PlanError, match="malformed"):
        MixPlan.from_dict({"version": 1, "tracks": [{"id": "x"}], "transitions": []})


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda p: setattr(p.tracks[0], "bpm", 5), "bpm"),
        (lambda p: setattr(p.tracks[0], "downbeat", 4), "downbeat"),
        (lambda p: setattr(p.tracks[1], "id", "t0"), "unique"),
        (lambda p: setattr(p.transitions[0], "overlap_beats", 1), "overlap_beats"),
        (lambda p: setattr(p.transitions[0], "style", "wobble"), "style"),
        (lambda p: setattr(p.transitions[0], "out_beat", 10_000), "runs past the end"),
        (lambda p: setattr(p.transitions[0], "in_beat", -1), "in_beat"),
        (lambda p: setattr(p.transitions[1], "out_beat", 10), "collide"),
        (lambda p: setattr(p, "bit_depth", 8), "bit_depth"),
        (lambda p: setattr(p, "ceiling_db", 3.0), "ceiling_db"),
        (lambda p: setattr(p.transitions[0].eq, "hi_in", (0.5, 0.2)), "hi_in"),
        (lambda p: p.transitions.pop(), "expected 2 transitions"),
        (lambda p: setattr(p.transitions[0], "to_track", "t2"), "in order"),
    ],
)
def test_validation_catches(mutate, message):
    plan = copy.deepcopy(_plan())
    mutate(plan)
    errs = validate(plan)
    assert errs, "expected a validation error"
    assert any(message in e for e in errs), errs
    with pytest.raises(PlanError):
        check(plan)
