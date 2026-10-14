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


# ---------- not throwing away the end of the outgoing track ----------
from blendscape.planner import DEFAULT_MAX_CUT_S, overlap_beats_for_seconds, unplayed_s  # noqa: E402


def metal_like(bpm=150.0):
    """A loud song followed by a long quiet ending that the section detector calls an outro."""
    return track("metal", [("intro", 4), ("body", 52), ("outro", 24)], bpm=bpm)


def cut_of(plan, i=0):
    tr = plan.transitions[i]
    return unplayed_s(plan.tracks[i], tr.out_beat, tr.overlap_beats)


def test_a_long_quiet_ending_no_longer_costs_the_end_of_the_track():
    """Regression: 'leave at the start of the outro' used to drop 27 s of a 24-bar ending at 150 BPM."""
    plan = make_plan([metal_like(), track("next", FULL, bpm=150.0)], overlap_beats=32)
    assert cut_of(plan) <= DEFAULT_MAX_CUT_S
    assert cut_of(plan) < 5  # in practice the new tail score pulls it much closer


def test_the_cap_is_a_hard_limit_and_the_soft_score_helps_even_without_it():
    for cap in (5.0, 10.0, 20.0):
        plan = make_plan([metal_like(), track("next", FULL, bpm=150.0)], overlap_beats=32, max_cut_s=cap)
        assert cut_of(plan) <= cap, cap
    uncapped = make_plan([metal_like(), track("next", FULL, bpm=150.0)], overlap_beats=32, max_cut_s=None)
    assert cut_of(uncapped) < 10  # the soft term alone already avoids the worst case
    zero = make_plan([metal_like(), track("next", FULL, bpm=150.0)], overlap_beats=32, max_cut_s=0)
    assert cut_of(zero) == pytest.approx(cut_of(uncapped))  # 0 means "no cap"


def test_a_coarse_phrase_grid_falls_back_to_shorter_phrases_to_honour_the_cap():
    # 80 bars at 150 BPM, overlap 8 bars: 8-bar phrase boundaries leave 12.8 s after the last overlap that fits
    a = track("a", [("body", 76)], bpm=150.0, tail_bars=0)  # ends at bar 76: grid of 8 bars => out at 64 (cut 4 bars)
    plan = make_plan([a, track("b", FULL, bpm=150.0)], overlap_beats=32, max_cut_s=3.0, phrase_bars=8)
    tr = plan.transitions[0]
    assert cut_of(plan) <= 3.0
    assert (tr.out_beat - plan.tracks[0].downbeat) % 32 != 0 and "grid to limit the cut" in tr.note


def test_says_so_when_the_cap_cannot_be_met():
    # the outgoing track ends with 4 minutes of music that cannot be reached by any overlap that fits
    a = track("a", [("body", 20)], bpm=150.0)
    a.active_end_s = a.duration_s  # explicit
    plan = make_plan([a, track("b", FULL, bpm=150.0)], overlap_beats=64, max_cut_s=0.5)
    assert "could not keep the cut" in plan.transitions[0].note or cut_of(plan) <= 0.5


def test_trailing_silence_is_not_counted_as_music_to_protect():
    a = track("a", FULL, bpm=120.0, tail_bars=15)  # 30 s of file after bar 56...
    a.active_end_s = a.duration_s - 30.0  # ...but the last 30 s are silent
    plan = make_plan([a, track("b", FULL)], overlap_beats=32)
    t = plan.transitions[0]
    assert plan.tracks[0].end_s == pytest.approx(a.duration_s - 30.0, abs=0.01)
    assert cut_of(plan) <= DEFAULT_MAX_CUT_S  # measured against the music, not the file
    assert t.out_beat == 4 * 48


def test_end_s_falls_back_to_the_duration_when_unknown():
    a = track("a", FULL)
    plan = make_plan([a, track("b", FULL)], overlap_beats=32)
    assert a.active_end_s == 0 and plan.tracks[0].end_s == plan.tracks[0].duration_s


def test_overlap_by_seconds_rounds_to_whole_bars_at_the_outgoing_tempo():
    assert overlap_beats_for_seconds(8, 150) == 20  # 20 beats at 150 BPM = 8.0 s
    assert overlap_beats_for_seconds(10, 120) == 20  # 10 s = 20 beats = 5 bars
    assert overlap_beats_for_seconds(5, 80) == 8  # 6.7 beats -> 2 bars
    assert overlap_beats_for_seconds(0.5, 120) == 4  # never less than a bar
    plan = make_plan([track("a", FULL, bpm=120.0), track("b", FULL, bpm=90.0), track("c", FULL, bpm=90.0)],
                     overlap_beats=32, overlap_seconds=10)
    assert [t.overlap_beats for t in plan.transitions] == [20, 16]  # 10 s at 120 BPM, then at 90 BPM (15 beats -> 16)
    for i, t in enumerate(plan.transitions):
        assert abs(t.overlap_beats * plan.tracks[i].grid.period_s - 10) < 2.5  # within half a bar of the target
    assert validate(plan) == []
