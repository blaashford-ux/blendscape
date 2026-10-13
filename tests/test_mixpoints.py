# SPDX-License-Identifier: GPL-3.0-or-later
"""Smart mix-point selection, driven by hand-built song structures (no audio)."""
import numpy as np
import pytest

from blendscape.analysis import TrackAnalysis
from blendscape.model import MixPlan, validate
from blendscape.planner import (
    Profile,
    make_plan,
    score_clash,
    score_in,
    score_out,
    tempo_gap,
)
from blendscape.sections import Section

BPM = 120.0  # 2 beats/s -> a bar is 2 s
LOW = {"intro": 0.05, "body": 1.0, "breakdown": 0.05, "outro": 0.4}


def track(name, structure, bpm=BPM, db=0, offset=0.25, tail_bars=1):
    """A TrackAnalysis with the given [(label, bars), ...] structure and matching per-bar bass levels."""
    secs, low, pos = [], [], 0
    for label, n in structure:
        secs.append({"label": label, "start_bar": pos, "end_bar": pos + n})
        low += [LOW[label]] * n
        pos += n
    bar_s = 4 * 60 / bpm
    return TrackAnalysis(path=f"/m/{name}.wav", sample_rate=44100, duration_s=offset + (pos + tail_bars) * bar_s,
                         bpm=bpm, offset_s=offset, downbeat=db, downbeat_confidence=1.5,
                         bar_low=low, sections=secs)


FULL = [("intro", 8), ("body", 16), ("breakdown", 8), ("body", 16), ("outro", 8)]


def test_leaves_from_the_outro_and_enters_through_the_intro_with_the_drop_landing_at_the_end():
    plan = make_plan([track("a", FULL), track("b", FULL)], overlap_beats=32)
    tr = plan.transitions[0]
    assert tr.out_beat == 4 * 48  # outro starts at bar 48
    assert tr.in_beat == 0  # B's 8-bar intro is exactly the overlap, drop lands as it ends
    assert "outro" in tr.note and "drop lands" in tr.note
    assert validate(plan) == []


def test_long_intro_enters_late_so_the_drop_still_lands_on_the_overlap_end():
    long_intro = [("intro", 16), ("body", 16), ("outro", 8)]
    plan = make_plan([track("a", FULL), track("b", long_intro)], overlap_beats=32)
    assert plan.transitions[0].in_beat == 4 * 8  # bars 8-16 of the intro, drop at bar 16


def test_does_not_bail_out_of_the_first_breakdown_when_an_outro_is_available():
    plan = make_plan([track("a", FULL), track("b", FULL)], overlap_beats=32)
    assert plan.transitions[0].out_beat > 4 * 24  # not the breakdown at bar 24


def test_leaves_at_a_breakdown_if_that_is_late_in_the_track_and_there_is_no_outro():
    a = [("intro", 8), ("body", 24), ("body", 8), ("breakdown", 8)]  # merged bodies; ends in a breakdown
    plan = make_plan([track("a", a), track("b", FULL)], overlap_beats=32)
    assert plan.transitions[0].out_beat == 4 * 40  # the breakdown start, last phrase that fits


def test_all_chosen_points_are_on_the_phrase_grid():
    tracks = [track("a", FULL, db=1), track("b", FULL, db=3), track("c", FULL, db=2)]
    plan = make_plan(tracks, overlap_beats=32)
    for i, tr in enumerate(plan.transitions):
        assert (tr.out_beat - plan.tracks[i].downbeat) % 32 == 0
        assert (tr.in_beat - plan.tracks[i + 1].downbeat) % 32 == 0


def test_runner_ups_are_recorded_best_first_and_distinct():
    plan = make_plan([track("a", FULL), track("b", FULL)], overlap_beats=32)
    tr = plan.transitions[0]
    assert tr.score is not None and 0 < tr.score <= 1
    assert 1 <= len(tr.alternatives) <= 3
    scores = [tr.score] + [alt["score"] for alt in tr.alternatives]
    assert scores == sorted(scores, reverse=True)
    pairs = {(tr.out_beat, tr.in_beat)} | {(a["out_beat"], a["in_beat"]) for a in tr.alternatives}
    assert len(pairs) == 1 + len(tr.alternatives)
    assert all(a["note"] for a in tr.alternatives)


def test_plan_with_scores_survives_json_roundtrip(tmp_path):
    plan = make_plan([track("a", FULL), track("b", FULL)], overlap_beats=32)
    f = tmp_path / "p.json"
    plan.save(f)
    again = MixPlan.load(f)
    assert again.transitions[0].alternatives == plan.transitions[0].alternatives
    assert again.transitions[0].score == plan.transitions[0].score
    assert again.tracks[0].sections == plan.tracks[0].sections


def test_latest_mode_ignores_structure_and_records_no_score():
    plan = make_plan([track("a", FULL), track("b", FULL)], overlap_beats=32, mix_points="latest")
    tr = plan.transitions[0]
    assert tr.score is None and tr.alternatives == [] and tr.in_beat == 0
    assert tr.out_beat + 32 + 32 > plan.tracks[0].n_beats - 1  # the very last phrase that fits
    with pytest.raises(ValueError):
        make_plan([track("a", FULL)], mix_points="random")


def test_middle_track_enters_early_enough_to_leave_again():
    long_b = [("intro", 8), ("body", 16), ("breakdown", 8), ("body", 16), ("outro", 8)]
    short_b = [("intro", 8), ("body", 8), ("outro", 8)]
    plan = make_plan([track("a", FULL), track("b", short_b), track("c", long_b)], overlap_beats=32)
    t1, t2 = plan.transitions
    assert t2.out_beat >= t1.in_beat + 32
    assert validate(plan) == []


def test_tracks_without_structure_fall_back_to_late_exit_and_early_entry():
    flat_a = track("a", [("body", 56)])
    flat_b = track("b", [("body", 56)])
    plan = make_plan([flat_a, flat_b], overlap_beats=32)
    tr = plan.transitions[0]
    assert tr.in_beat == 0
    assert tr.out_beat >= 4 * 40  # late in the track


def test_analysis_without_sections_still_plans():
    a, b = track("a", FULL), track("b", FULL)
    a.sections, b.sections = [], []
    plan = make_plan([a, b], overlap_beats=32)
    assert validate(plan) == []


def test_score_out_and_in_ordering():
    secs = [Section("intro", 0, 8), Section("body", 8, 24), Section("breakdown", 24, 32), Section("body", 32, 48),
            Section("outro", 48, 56)]
    assert score_out(secs, 48, 8)[0] > score_out(secs, 40, 8)[0] > 0
    assert score_out(secs, 48, 8)[0] > score_out(secs, 8, 8)[0]  # outro beats the middle of a body
    assert score_out(secs, 24, 8)[0] > score_out(secs, 8, 8)[0]  # breakdown beats the middle of a body
    assert score_in(secs, 0, 8)[0] > score_in(secs, 16, 8)[0]  # intro start beats mid-body
    assert score_in(secs, 0, 8)[0] == pytest.approx(1.0)  # intro start + drop lands as the overlap ends
    assert score_in(secs, 0, 4)[0] < score_in(secs, 0, 8)[0]


def test_bass_clash_is_low_when_both_tracks_are_heavy_together():
    heavy = Profile(8, [Section("body", 0, 8)], np.ones(8))
    light = Profile(8, [Section("intro", 0, 8)], np.full(8, 0.05))
    assert score_clash(heavy, heavy, 0, 0, 8) == pytest.approx(0.0)
    assert score_clash(heavy, light, 0, 0, 8) > 0.9
    assert score_clash(light, light, 0, 0, 8) > 0.9
    assert score_clash(heavy, heavy, 6, 6, 8) == pytest.approx(0.0)  # window running off the end is clipped


def test_tempo_gap():
    a, b = make_plan([track("a", FULL, bpm=120), track("b", FULL, bpm=130)], overlap_beats=32).tracks
    assert tempo_gap(a, b) == pytest.approx(130 / 120 - 1)
