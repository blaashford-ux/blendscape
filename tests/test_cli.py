# SPDX-License-Identifier: GPL-3.0-or-later
import json

import pytest
import soundfile as sf

from blendscape.cli import main
from conftest import needs_rubberband


def test_analyze_prints_tempo(tracks, capsys):
    assert main(["analyze", tracks["a"][0]]) == 0
    out = capsys.readouterr().out
    assert "124.0" in out and "BPM" in out


def test_analyze_json(tracks, capsys):
    assert main(["analyze", "--json", tracks["a"][0], tracks["b"][0]]) == 0
    data = json.loads(capsys.readouterr().out)
    assert [round(d["bpm"]) for d in data] == [124, 128]


def test_analyze_lists_the_detected_sections(tracks, capsys):
    assert main(["analyze", tracks["a"][0]]) == 0
    out = capsys.readouterr().out
    assert "intro 0-8" in out and "body 8-" in out


def test_plan_explain_shows_score_and_runner_ups(tracks, tmp_path, capsys):
    plan = tmp_path / "plan.json"
    assert main(["plan", tracks["a"][0], tracks["b"][0], "-o", str(plan), "--overlap-beats", "16", "--explain"]) == 0
    out = capsys.readouterr().out
    assert "score" in out and "alt:" in out
    d = json.loads(plan.read_text())
    assert d["transitions"][0]["score"] > 0 and d["transitions"][0]["alternatives"]
    assert d["tracks"][0]["sections"]


def test_plan_without_explain_hides_runner_ups(tracks, tmp_path, capsys):
    plan = tmp_path / "plan.json"
    main(["plan", tracks["a"][0], tracks["b"][0], "-o", str(plan), "--overlap-beats", "16"])
    out = capsys.readouterr().out
    assert "score" in out and "alt:" not in out


def test_latest_mix_points_option(tracks, tmp_path, capsys):
    plan = tmp_path / "plan.json"
    assert main(["plan", tracks["a"][0], tracks["b"][0], "-o", str(plan), "--mix-points", "latest"]) == 0
    assert json.loads(plan.read_text())["transitions"][0]["score"] is None


def test_big_tempo_gap_is_called_out(tracks, tmp_path, capsys):
    import soundfile as sf

    from synth import make_track

    slow = tmp_path / "slow.wav"
    make_track(slow, 100.0, 0.2, 55.0)
    plan = tmp_path / "plan.json"
    assert main(["plan", tracks["a"][0], str(slow), "-o", str(plan), "--overlap-beats", "16"]) == 0
    assert "WARNING: tempo gap" in capsys.readouterr().out
    assert sf.info(slow).duration > 10


def test_limiter_flags_reach_the_plan(tracks, tmp_path, monkeypatch):
    from blendscape import cli

    seen = {}

    def fake_render(plan, progress=None):
        import numpy as np

        from blendscape.render import RenderReport

        seen["lim"] = plan.limiter
        seen["ceil"] = plan.ceiling_db
        return np.zeros((100, 2), np.float32), RenderReport(duration_s=0.002)

    monkeypatch.setattr(cli, "render", fake_render)
    monkeypatch.setattr(cli, "available", lambda: True)
    monkeypatch.setattr(cli, "backend", lambda: "cli")
    plan = tmp_path / "plan.json"
    main(["plan", tracks["a"][0], tracks["b"][0], "-o", str(plan), "--overlap-beats", "16"])
    assert main(["render", str(plan), "-o", str(tmp_path / "o.wav"), "--no-limiter", "--release-ms", "300",
                 "--sample-peak", "--ceiling-db", "-2"]) == 0
    assert seen["lim"].enabled is False and seen["lim"].release_ms == 300 and seen["lim"].true_peak is False
    assert seen["ceil"] == -2


def test_plan_then_validate(tracks, tmp_path, capsys):
    plan = tmp_path / "plan.json"
    assert main(["plan", tracks["a"][0], tracks["b"][0], "-o", str(plan), "--overlap-beats", "16"]) == 0
    d = json.loads(plan.read_text())
    assert len(d["tracks"]) == 2 and d["transitions"][0]["overlap_beats"] == 16
    capsys.readouterr()
    assert main(["validate", str(plan)]) == 0
    assert "bass_swap" in capsys.readouterr().out


def test_validate_reports_problems_and_exit_code(tracks, tmp_path, capsys):
    plan = tmp_path / "plan.json"
    main(["plan", tracks["a"][0], tracks["b"][0], "-o", str(plan)])
    d = json.loads(plan.read_text())
    d["transitions"][0]["out_beat"] = 99999
    plan.write_text(json.dumps(d))
    capsys.readouterr()
    assert main(["validate", str(plan)]) == 1
    assert "runs past the end" in capsys.readouterr().err


def test_validate_flags_missing_audio(tracks, tmp_path, capsys):
    plan = tmp_path / "plan.json"
    main(["plan", tracks["a"][0], tracks["b"][0], "-o", str(plan)])
    d = json.loads(plan.read_text())
    d["tracks"][0]["path"] = "nowhere/missing.wav"
    plan.write_text(json.dumps(d))
    assert main(["validate", str(plan)]) == 1


def test_errors_are_friendly_not_tracebacks(tmp_path, capsys):
    assert main(["analyze", str(tmp_path / "nope.wav")]) == 1
    assert "error: no such file" in capsys.readouterr().err
    bad = tmp_path / "bad.json"
    bad.write_text("{")
    assert main(["validate", str(bad)]) == 1


def test_missing_rubberband_gives_a_clear_message(tracks, tmp_path, monkeypatch, capsys):
    from blendscape import stretch

    monkeypatch.setattr(stretch, "_cli_path", lambda: None)
    monkeypatch.setattr(stretch, "_lib", None)
    plan = tmp_path / "plan.json"
    main(["plan", tracks["a"][0], tracks["b"][0], "-o", str(plan)])
    capsys.readouterr()
    assert main(["render", str(plan), "-o", str(tmp_path / "o.wav")]) == 2
    assert "Rubber Band not found" in capsys.readouterr().err


@pytest.mark.slow
@needs_rubberband
def test_mix_end_to_end(tracks, tmp_path, capsys):
    out = tmp_path / "out" / "mix.wav"
    keep = tmp_path / "plan.json"
    rc = main(["mix", tracks["a"][0], tracks["b"][0], "-o", str(out), "--overlap-beats", "16",
               "--bit-depth", "16", "--save-plan", str(keep)])
    assert rc == 0 and out.exists() and keep.exists()
    info = sf.info(out)
    assert info.subtype == "PCM_16" and info.samplerate == 44100 and info.channels == 2
    assert 76 < info.duration < 81  # A leaves at 0:31.3, B (47 s) plays out in full
    # render the saved plan again with a different depth
    out2 = tmp_path / "again.wav"
    assert main(["render", str(keep), "-o", str(out2), "--bit-depth", "24"]) == 0
    assert sf.info(out2).subtype == "PCM_24"
