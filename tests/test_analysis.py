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
