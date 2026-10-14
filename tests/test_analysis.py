# SPDX-License-Identifier: GPL-3.0-or-later
import numpy as np
import pytest
import soundfile as sf

from blendscape import analysis
from blendscape.analysis import AnalysisError, analyze


@pytest.mark.parametrize("name", ["a", "b", "c"])
def test_tempo_grid_and_downbeat_match_the_synthetic_truth(tracks, name):
    path, bpm, offset = tracks[name]
    r = analyze(path)
    period = 60 / bpm
    assert r.bpm == pytest.approx(bpm, abs=0.02)
    # bar 1 of the synthetic track starts at `offset`; our first downbeat must coincide modulo whole bars
    db_time = r.offset_s + r.downbeat * (60 / r.bpm)
    err = (db_time - offset + 2 * period) % (4 * period) - 2 * period
    assert abs(err) < 0.003, f"downbeat off by {err * 1000:.1f} ms"
    assert r.duration_s == pytest.approx(sf.info(path).duration)
    assert len(r.bar_rms) > 20


def test_results_are_cached_by_content(tracks, tmp_path, monkeypatch):
    path = tracks["a"][0]
    first = analyze(path, cache_dir=tmp_path / "c")
    assert list((tmp_path / "c").glob("*.json"))
    monkeypatch.setattr(analysis, "_analyze_uncached", lambda p: pytest.fail("should have used the cache"))
    again = analyze(path, cache_dir=tmp_path / "c")
    assert again.bpm == first.bpm and again.offset_s == first.offset_s


def test_cache_can_be_bypassed(tracks, tmp_path):
    analyze(tracks["a"][0], cache_dir=tmp_path / "c", use_cache=False)
    assert not (tmp_path / "c").exists()


def test_missing_and_too_short_files(tmp_path):
    with pytest.raises(AnalysisError, match="no such file"):
        analyze(tmp_path / "nope.wav")
    p = tmp_path / "short.wav"
    sf.write(p, np.zeros((44100 * 3, 2), np.float32), 44100)
    with pytest.raises(AnalysisError, match="at least"):
        analyze(p)


def test_beatless_audio_is_reported_not_crashed(tmp_path):
    p = tmp_path / "noise.wav"
    x = np.random.default_rng(1).standard_normal((44100 * 20, 2)).astype(np.float32) * 0.0001
    sf.write(p, x, 44100)
    try:
        analyze(p)  # may "succeed" with a meaningless grid, but must not raise anything but AnalysisError
    except AnalysisError:
        pass


def test_song_structure_is_detected_on_the_synthetic_tracks(tracks):
    r = analyze(tracks["a"][0])
    secs = [(s["label"], s["start_bar"], s["end_bar"]) for s in r.sections]
    # synthetic layout: intro 8 bars | body 8 bars | outro 8 bars (the kick fades through the outro)
    assert [s[0] for s in secs] == ["intro", "body", "outro"]
    assert secs[0][:2] == ("intro", 0) and secs[0][2] == 8
    assert secs[1][1] == 8
    # The kick fades out linearly from bar 16, and the detector only calls it an outro once the loudness has
    # dropped clearly below the body, so a gradual fade is labelled late (known limitation, see docs/PLAN.md).
    assert 16 <= secs[2][1] < 24
    assert secs[-1][2] == len(r.bar_low)
    assert len(r.bar_low) == len(r.bar_mid) == len(r.bar_high) == len(r.bar_rms)
    # kick and bass live in the low band: silent in the intro, strong in the body
    assert max(r.bar_low[:8]) < 0.1 * max(r.bar_low[8:16])


def test_full_pipeline_finds_a_bar_line_the_old_accent_rule_could_not(rich_set):
    """bass_root has its only bar-line cue in the sub-bass (a long root note on beat 1), which the old mid-band
    accent rule never heard."""
    t = next(x for x in rich_set if x.style == "bass_root")
    r = analyze(t.path)
    assert r.downbeat == t.downbeat
    assert r.downbeat_confidence > 0.5 and sum(r.downbeat_probs) == pytest.approx(1.0)
    assert not r.downbeat_manual


def test_with_downbeat_recomputes_bars_and_marks_it_manual(tracks):
    from blendscape.analysis import with_downbeat

    r = analyze(tracks["a"][0])
    forced = with_downbeat(r, (r.downbeat + 1) % 4)
    assert forced.downbeat == (r.downbeat + 1) % 4
    assert forced.downbeat_manual and forced.downbeat_confidence == 1.0
    assert forced.downbeat_probs[forced.downbeat] == 1.0
    assert forced.bpm == r.bpm and forced.offset_s == r.offset_s  # only the bar line moved
    assert forced.bar_low != r.bar_low  # the bars were re-cut one beat later
    assert forced.sections and forced.sections[0]["start_bar"] == 0
    same = with_downbeat(r, r.downbeat)
    assert same.downbeat_manual and same.downbeat == r.downbeat
    with pytest.raises(AnalysisError, match="0, 1, 2 or 3"):
        with_downbeat(r, 4)


def test_active_end_ignores_trailing_silence_and_very_quiet_fades():
    from blendscape.analysis import active_end

    sr = 44100
    rng = np.random.default_rng(0)
    loud = (rng.standard_normal(sr * 20) * 0.3).astype(np.float32)
    assert active_end(np.concatenate([loud, np.zeros(sr * 10, np.float32)]), sr) == pytest.approx(20.0, abs=0.1)
    # a tail 60 dB down is below the 45 dB floor and does not count
    quiet = (rng.standard_normal(sr * 10) * 0.0003).astype(np.float32)
    assert active_end(np.concatenate([loud, quiet]), sr) == pytest.approx(20.0, abs=0.1)
    # a tail 30 dB down is music (a ring-out) and does count
    ring = (rng.standard_normal(sr * 10) * 0.01).astype(np.float32)
    assert active_end(np.concatenate([loud, ring]), sr) == pytest.approx(30.0, abs=0.1)
    assert active_end(np.zeros(sr * 5, np.float32), sr) == pytest.approx(5.0, abs=0.05)  # nothing to trim against


def test_analysis_records_where_the_music_ends(tracks):
    import soundfile as sf

    r = analyze(tracks["a"][0])
    assert 0 < r.active_end_s <= r.duration_s + 1e-6
    assert r.active_end_s > r.duration_s - 3.0  # the synthetic tracks end with only ~2 s of room noise
    quiet_end = tracks["a"][0]
    x, sr = sf.read(quiet_end, dtype="float32", always_2d=True)
    assert r.duration_s == pytest.approx(len(x) / sr)
