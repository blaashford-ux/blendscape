# SPDX-License-Identifier: GPL-3.0-or-later
import numpy as np

from blendscape.sections import Section, detect_sections, is_drop, section_at

# (label, bars, (low, mid, high) band levels)
CLASSIC = [
    ("intro", 8, (0.02, 0.30, 0.20)),
    ("body", 16, (1.00, 0.50, 0.25)),
    ("breakdown", 8, (0.02, 0.45, 0.05)),
    ("body", 16, (1.00, 0.50, 0.25)),
    ("outro", 8, (0.30, 0.20, 0.10)),
]


def bars(structure, noise=0.0, seed=0):
    rng = np.random.default_rng(seed)
    cols = [[], [], []]
    for _label, n, levels in structure:
        for lv, col in zip(levels, cols, strict=True):
            col.extend(lv * (1 + noise * rng.standard_normal(n)))
    return [np.maximum(np.array(c), 1e-5) for c in cols]


def labels(secs):
    return [(s.label, s.start_bar, s.end_bar) for s in secs]


def test_classic_dance_structure_is_found_exactly():
    secs = detect_sections(*bars(CLASSIC))
    assert labels(secs) == [("intro", 0, 8), ("body", 8, 24), ("breakdown", 24, 32), ("body", 32, 48),
                            ("outro", 48, 56)]


def test_detection_survives_level_noise():
    secs = detect_sections(*bars(CLASSIC, noise=0.08, seed=3))
    assert [s.label for s in secs] == ["intro", "body", "breakdown", "body", "outro"]
    for got, want in zip(secs, [0, 8, 24, 32, 48], strict=True):
        assert abs(got.start_bar - want) <= 1


def test_boundaries_snap_to_the_phrase_grid():
    shifted = [("intro", 9, (0.02, 0.30, 0.20)), ("body", 15, (1.0, 0.5, 0.25)),
               ("breakdown", 8, (0.02, 0.45, 0.05)), ("body", 16, (1.0, 0.5, 0.25)), ("outro", 8, (0.3, 0.2, 0.1))]
    secs = detect_sections(*bars(shifted))
    assert secs[1].start_bar == 8  # change at bar 9 snaps to the phrase line at bar 8
    # but a change far from any phrase line is left where it is
    odd = [("intro", 12, (0.02, 0.30, 0.20)), ("body", 20, (1.0, 0.5, 0.25))]
    assert detect_sections(*bars(odd))[1].start_bar == 12


def test_track_that_starts_loud_has_no_intro():
    s = [("body", 16, (1.0, 0.5, 0.25)), ("breakdown", 8, (0.02, 0.45, 0.05)), ("body", 16, (1.0, 0.5, 0.25)),
         ("outro", 8, (0.3, 0.2, 0.1))]
    secs = detect_sections(*bars(s))
    assert secs[0].label == "body" and secs[-1].label == "outro"
    assert "intro" not in [x.label for x in secs]


def test_steady_track_is_one_body():
    s = [("body", 40, (1.0, 0.5, 0.25))]
    assert labels(detect_sections(*bars(s, noise=0.03))) == [("body", 0, 40)]


def test_degenerate_inputs():
    assert detect_sections([], [], []) == []
    assert labels(detect_sections([1, 1, 1], [1, 1, 1], [1, 1, 1])) == [("body", 0, 3)]


def test_sections_tile_the_track_without_gaps():
    secs = detect_sections(*bars(CLASSIC, noise=0.05, seed=9))
    assert secs[0].start_bar == 0 and secs[-1].end_bar == 56
    for a, b in zip(secs, secs[1:], strict=False):
        assert a.end_bar == b.start_bar
        assert a.label != b.label  # neighbours with the same label are merged


def test_helpers():
    secs = detect_sections(*bars(CLASSIC))
    assert section_at(secs, 30).label == "breakdown"
    assert section_at(secs, 999) is None
    drops = [i for i in range(len(secs)) if is_drop(secs, i)]
    assert [secs[i].start_bar for i in drops] == [8, 32]  # body after intro, body after breakdown
    assert Section.from_dict(secs[0].to_dict()) == secs[0]
