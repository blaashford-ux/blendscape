# SPDX-License-Identifier: GPL-3.0-or-later
import json

import pytest
import soundfile as sf

from conftest import needs_rubberband, wait_job

SR = 44100


# ---------- system ----------
def test_health_reports_the_stretch_backend(client):
    d = client.get("/api/health").json()
    assert d["ok"] and d["version"] and "home" in d
    assert d["stretch_backend"] in ("cli", "ctypes", None)


def test_root_explains_itself_when_the_web_ui_is_not_built(api_home, tmp_path):
    from fastapi.testclient import TestClient

    from blendscape.server.app import create_app

    with TestClient(create_app(home=api_home, web_dir=tmp_path / "no-build")) as c:
        d = c.get("/").json()
    assert d["docs"] == "/docs" and d["web_ui"] == "not built yet"


def test_root_serves_the_built_web_ui_next_to_the_api(api_home, tmp_path):
    from fastapi.testclient import TestClient

    from blendscape.server.app import create_app

    (tmp_path / "index.html").write_text("<!doctype html><title>Blendscape UI</title>")
    with TestClient(create_app(home=api_home, web_dir=tmp_path)) as c:
        assert "Blendscape UI" in c.get("/").text
        assert c.get("/api/health").json()["ok"] is True  # the API is not shadowed by the UI


def test_cors_allows_the_local_dev_server_and_nothing_else(client):
    ok = client.options("/api/health", headers={"Origin": "http://localhost:5173",
                                                "Access-Control-Request-Method": "GET"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:5173"
    bad = client.options("/api/health", headers={"Origin": "https://evil.example",
                                                 "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in bad.headers


# ---------- projects ----------
def test_project_lifecycle(client):
    p = client.post("/api/projects", json={"name": "Friday set"}).json()
    assert p["id"].startswith("p_") and p["name"] == "Friday set" and p["tracks"] == [] and p["plan"] is None
    assert p["settings"]["overlap_beats"] == 32 and p["settings"]["max_cut_seconds"] == 10
    assert p["id"] in [x["id"] for x in client.get("/api/projects").json()]
    assert client.patch(f"/api/projects/{p['id']}", json={"name": "Saturday"}).json()["name"] == "Saturday"
    assert client.delete(f"/api/projects/{p['id']}").status_code == 204
    assert client.get(f"/api/projects/{p['id']}").status_code == 404


@pytest.mark.parametrize("bad", ["p_nope", "x", "p_../etc", "..", "p_%2e%2e"])
def test_unknown_or_hostile_project_ids_are_404(client, bad):
    assert client.get(f"/api/projects/{bad}").status_code in (404, 405)


def test_project_names_are_validated(client):
    assert client.post("/api/projects", json={"name": ""}).status_code == 422
    assert client.post("/api/projects", json={"name": "x" * 200}).status_code == 422


def test_projects_survive_a_restart(api_home, client):
    from fastapi.testclient import TestClient

    from blendscape.server.app import create_app

    pid = client.post("/api/projects", json={"name": "persist me"}).json()["id"]
    client.patch(f"/api/projects/{pid}/settings", json={"overlap_seconds": 9, "style": "crossfade"})
    with TestClient(create_app(home=api_home)) as fresh:
        p = fresh.get(f"/api/projects/{pid}").json()
    assert p["name"] == "persist me"
    assert p["settings"]["overlap_seconds"] == 9 and p["settings"]["style"] == "crossfade"


# ---------- settings ----------
def test_settings_patch_merges_and_validates(client):
    pid = client.post("/api/projects", json={}).json()["id"]
    s = client.patch(f"/api/projects/{pid}/settings", json={"overlap_beats": 16, "bit_depth": 16}).json()["settings"]
    assert s["overlap_beats"] == 16 and s["bit_depth"] == 16 and s["style"] == "bass_swap"
    s = client.patch(f"/api/projects/{pid}/settings", json={"limiter": {"release_ms": 300}}).json()["settings"]
    assert s["limiter"]["release_ms"] == 300 and s["overlap_beats"] == 16
    s = client.patch(f"/api/projects/{pid}/settings", json={"overlap_seconds": 8}).json()["settings"]
    assert s["overlap_seconds"] == 8
    s = client.patch(f"/api/projects/{pid}/settings", json={"clear_overlap_seconds": True}).json()["settings"]
    assert s["overlap_seconds"] is None
    for bad in ({"overlap_beats": 1}, {"style": "wobble"}, {"bit_depth": 12}, {"ceiling_db": 3}, {"nonsense": 1},
                {"max_cut_seconds": -1}, {"sample_rate": 12345}, {"limiter": {"release_ms": 1}}):
        assert client.patch(f"/api/projects/{pid}/settings", json=bad).status_code == 422, bad


# ---------- tracks ----------
def test_add_a_track_by_path(client, tracks):
    pid = client.post("/api/projects", json={}).json()["id"]
    r = client.post(f"/api/projects/{pid}/tracks", json={"path": tracks["a"][0]})
    t = r.json()
    assert r.status_code == 201 and t["id"].startswith("t_") and t["name"].endswith("a_124.wav")
    assert t["samplerate"] == SR and t["duration_s"] > 40 and t["source"] == "path" and not t["analysed"]


def test_bad_paths_and_non_audio_are_rejected_clearly(client, tmp_path):
    pid = client.post("/api/projects", json={}).json()["id"]
    r = client.post(f"/api/projects/{pid}/tracks", json={"path": str(tmp_path / "missing.wav")})
    assert r.status_code == 422 and "no such file" in r.json()["detail"]
    txt = tmp_path / "notes.txt"
    txt.write_text("not audio")
    r = client.post(f"/api/projects/{pid}/tracks", json={"path": str(txt)})
    assert r.status_code == 422 and "not an audio file" in r.json()["detail"]
    r = client.post(f"/api/projects/{pid}/tracks", json={"path": str(tmp_path)})  # a directory
    assert r.status_code == 422
    assert client.get(f"/api/projects/{pid}").json()["tracks"] == []


def test_upload_keeps_a_copy_and_sanitises_the_name(client, tracks, api_home):
    pid = client.post("/api/projects", json={}).json()["id"]
    data = open(tracks["b"][0], "rb").read()
    r = client.post(f"/api/projects/{pid}/tracks/upload", files={"file": ("../../evil name?.wav", data, "audio/wav")})
    t = r.json()
    assert r.status_code == 201 and t["source"] == "upload"
    assert "/" not in t["name"] and ".." not in t["name"] and t["name"].endswith(".wav")
    audio_dir = api_home / "projects" / pid / "audio"
    stored = list(audio_dir.iterdir())
    assert len(stored) == 1 and stored[0].stat().st_size == len(data)
    # removing the track removes the copy
    assert client.delete(f"/api/projects/{pid}/tracks/{t['id']}").status_code == 204
    assert not list(audio_dir.iterdir())


def test_uploaded_tracks_keep_the_name_you_gave_them_everywhere(client, tracks):
    pid = client.post("/api/projects", json={}).json()["id"]
    client.patch(f"/api/projects/{pid}/settings", json={"overlap_beats": 16})
    for n, label in (("a", "first song.wav"), ("b", "second.wav")):
        data = open(tracks[n][0], "rb").read()
        r = client.post(f"/api/projects/{pid}/tracks/upload", files={"file": (label, data, "audio/wav")})
        assert r.status_code == 201
    assert wait_job(client, client.post(f"/api/projects/{pid}/analyze").json()["id"])["state"] == "done"
    p = client.get(f"/api/projects/{pid}").json()
    assert [t["name"] for t in p["tracks"]] == ["first song.wav", "second.wav"]
    # (not the stored "u_1a2b3c4d-first song.wav")
    assert [t["name"] for t in p["plan"]["tracks"]] == ["first song.wav", "second.wav"]
    assert all("u_" not in w for t in p["plan"]["transitions"] for w in t["warnings"])


def test_upload_of_something_that_is_not_audio_leaves_nothing_behind(client, api_home):
    pid = client.post("/api/projects", json={}).json()["id"]
    r = client.post(f"/api/projects/{pid}/tracks/upload", files={"file": ("x.wav", b"RIFF-not-really", "audio/wav")})
    assert r.status_code == 422
    assert not list((api_home / "projects" / pid / "audio").iterdir())


def test_removing_a_path_track_never_deletes_the_users_file(client, tracks):
    import os

    pid = client.post("/api/projects", json={}).json()["id"]
    t = client.post(f"/api/projects/{pid}/tracks", json={"path": tracks["c"][0]}).json()
    assert client.delete(f"/api/projects/{pid}/tracks/{t['id']}").status_code == 204
    assert os.path.exists(tracks["c"][0])
    assert client.delete(f"/api/projects/{pid}/tracks/{t['id']}").status_code == 404


def test_reorder_validates_and_marks_the_plan_stale(client, planned):
    pid = planned["id"]
    ids = [t["id"] for t in planned["tracks"]]
    assert planned["plan"] and not planned["plan_stale"]
    assert client.put(f"/api/projects/{pid}/tracks/order", json={"order": ids[:2]}).status_code == 422
    assert client.put(f"/api/projects/{pid}/tracks/order", json={"order": ids + ids[:1]}).status_code == 422
    r = client.put(f"/api/projects/{pid}/tracks/order", json={"order": list(reversed(ids))})
    assert r.status_code == 200 and [t["id"] for t in r.json()["tracks"]] == list(reversed(ids))
    assert r.json()["plan_stale"]


# ---------- analysis and planning ----------
def test_planning_before_analysis_is_a_409_that_says_what_to_do(client, project):
    r = client.post(f"/api/projects/{project['id']}/plan")
    assert r.status_code == 409 and "not been analysed" in r.json()["detail"]
    assert client.post("/api/projects", json={}).status_code == 201
    empty = client.post("/api/projects", json={}).json()["id"]
    assert client.post(f"/api/projects/{empty}/analyze").status_code == 409


def test_analysis_job_analyses_every_track_and_plans(client, project):
    pid = project["id"]
    client.patch(f"/api/projects/{pid}/settings", json={"overlap_beats": 16})
    j = client.post(f"/api/projects/{pid}/analyze")
    assert j.status_code == 202
    job = wait_job(client, j.json()["id"])
    assert job["state"] == "done" and job["result"] == {"tracks": 3, "planned": True}
    p = client.get(f"/api/projects/{pid}").json()
    assert all(t["analysed"] for t in p["tracks"])
    bpms = [round(t["analysis"]["bpm"]) for t in p["tracks"]]
    assert bpms == [124, 128, 126]
    plan = p["plan"]
    assert [t["id"] for t in plan["tracks"]] == [t["id"] for t in p["tracks"]]  # stable project ids, not file-derived
    assert len(plan["transitions"]) == 2 and not p["plan_stale"]
    tr = plan["transitions"][0]
    assert tr["overlap_beats"] == 16 and tr["end_s"] > tr["start_s"] and tr["from"] == p["tracks"][0]["id"]
    assert tr["unplayed_tail_s"] >= 0 and tr["alternatives"] and tr["score"] is not None
    assert plan["total_duration_s"] > 100
    a = client.get(f"/api/projects/{pid}/tracks/{p['tracks'][0]['id']}/analysis").json()
    assert a["bpm"] == pytest.approx(124, abs=0.1) and a["sections"]


def test_analysis_without_replan_leaves_the_plan_alone(client, project):
    j = client.post(f"/api/projects/{project['id']}/analyze", json={"replan": False}).json()
    job = wait_job(client, j["id"])
    assert job["result"]["planned"] is False
    assert client.get(f"/api/projects/{project['id']}").json()["plan"] is None


def test_progress_events_stream_to_the_end(client, project):
    pid = project["id"]
    j = client.post(f"/api/projects/{pid}/analyze", json={"replan": False}).json()
    events = []
    with client.stream("GET", f"/api/jobs/{j['id']}/events") as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        for line in r.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    assert events and events[-1]["state"] == "done" and events[-1]["result"]["tracks"] == 3
    assert [e["seq"] for e in events] == sorted(e["seq"] for e in events)
    assert any("analysing" in e["message"] for e in events)
    fractions = [e["fraction"] for e in events if e["fraction"] is not None]
    assert fractions == sorted(fractions) and fractions[-1] == 1.0


def test_changing_settings_makes_the_plan_stale_and_replanning_fixes_it(client, planned):
    pid = planned["id"]
    p = client.patch(f"/api/projects/{pid}/settings", json={"overlap_beats": 8}).json()
    assert p["plan_stale"]
    p = client.post(f"/api/projects/{pid}/plan").json()
    assert not p["plan_stale"] and p["plan"]["transitions"][0]["overlap_beats"] == 8


def test_overlap_seconds_setting_drives_the_plan(client, planned):
    pid = planned["id"]
    client.patch(f"/api/projects/{pid}/settings", json={"overlap_seconds": 6})
    p = client.post(f"/api/projects/{pid}/plan").json()
    for tr in p["plan"]["transitions"]:
        assert tr["overlap_beats"] % 4 == 0 and abs(tr["overlap_s"] - 6) < 2


# ---------- editing the plan ----------
def test_editing_a_transition(client, planned):
    pid = planned["id"]
    tr0 = planned["plan"]["transitions"][0]
    r = client.patch(f"/api/projects/{pid}/plan/transitions/0", json={"overlap_beats": 8, "style": "crossfade"})
    assert r.status_code == 200
    tr = r.json()["plan"]["transitions"][0]
    assert tr["overlap_beats"] == 8 and tr["style"] == "crossfade" and tr["note"] == "edited by hand"
    assert tr["score"] is None and tr["out_beat"] == tr0["out_beat"]
    r = client.patch(f"/api/projects/{pid}/plan/transitions/0", json={"eq": {"bass_swap_center": 0.3}})
    assert r.json()["plan"]["transitions"][0]["eq"]["bass_swap_center"] == 0.3


def test_an_invalid_edit_is_refused_and_changes_nothing(client, planned):
    pid = planned["id"]
    before = client.get(f"/api/projects/{pid}/plan").json()["plan"]
    r = client.patch(f"/api/projects/{pid}/plan/transitions/0", json={"out_beat": 99999})
    assert r.status_code == 422 and "runs past the end" in r.json()["errors"][0]
    r = client.patch(f"/api/projects/{pid}/plan/transitions/0", json={"eq": {"hi_in": [0.9, 0.1]}})
    assert r.status_code == 422
    assert client.patch(f"/api/projects/{pid}/plan/transitions/0", json={"wat": 1}).status_code == 422
    assert client.patch(f"/api/projects/{pid}/plan/transitions/9", json={"overlap_beats": 8}).status_code == 404
    assert client.get(f"/api/projects/{pid}/plan").json()["plan"] == before


def test_choosing_an_alternative(client, planned):
    pid = planned["id"]
    tr = planned["plan"]["transitions"][0]
    alt = tr["alternatives"][0]
    p = client.post(f"/api/projects/{pid}/plan/transitions/0/alternative", json={"index": 0}).json()
    new = p["plan"]["transitions"][0]
    assert (new["out_beat"], new["in_beat"]) == (alt["out_beat"], alt["in_beat"])
    assert "alternatives" in new["note"]
    r = client.post(f"/api/projects/{pid}/plan/transitions/0/alternative", json={"index": 50})
    assert r.status_code == 422


def test_edits_survive_replanning_and_can_be_reset(client, planned):
    pid = planned["id"]
    client.patch(f"/api/projects/{pid}/plan/transitions/1", json={"overlap_beats": 12})
    client.patch(f"/api/projects/{pid}/settings", json={"overlap_beats": 8})
    p = client.post(f"/api/projects/{pid}/plan").json()
    assert p["plan"]["transitions"][1]["overlap_beats"] == 12  # the hand edit wins over the new global setting
    assert p["plan"]["transitions"][0]["overlap_beats"] == 8
    assert [t["edited"] for t in p["plan"]["transitions"]] == [False, True]
    p = client.delete(f"/api/projects/{pid}/plan/transitions/1/override").json()
    assert [t["edited"] for t in p["plan"]["transitions"]] == [False, False]
    assert p["plan"]["transitions"][1]["overlap_beats"] == 8 and p["plan"]["transitions"][1]["note"] != "edited by hand"


def test_an_edit_that_no_longer_fits_is_dropped_with_an_explanation(client, planned):
    pid = planned["id"]
    mid = planned["tracks"][1]
    t0 = planned["plan"]["transitions"][0]
    # edit B->C to leave at the earliest moment B has finished entering...
    earliest = t0["in_beat"] + t0["overlap_beats"]
    r = client.patch(f"/api/projects/{pid}/plan/transitions/1", json={"out_beat": earliest})
    assert r.status_code == 200, r.text
    # ...then move B's bar line later, which pushes the end of its entrance past that edit
    db = mid["analysis"]["downbeat"]
    assert db < 3, "fixture assumption: the middle track's detected bar line is not already the last beat"
    client.put(f"/api/projects/{pid}/tracks/{mid['id']}/downbeat", json={"downbeat": 3})
    p = client.post(f"/api/projects/{pid}/plan").json()
    assert p["plan_warnings"] and "no longer fits" in p["plan_warnings"][0] and mid["name"] in p["plan_warnings"][0]
    tr1 = p["plan"]["transitions"][1]
    assert tr1["note"] != "edited by hand" and tr1["out_beat"] >= p["plan"]["transitions"][0]["in_beat"] + 16


def test_overrides_for_pairs_that_no_longer_exist_are_forgotten(client, planned):
    pid = planned["id"]
    ids = [t["id"] for t in planned["tracks"]]
    client.patch(f"/api/projects/{pid}/plan/transitions/0", json={"overlap_beats": 12})
    client.delete(f"/api/projects/{pid}/tracks/{ids[1]}")
    p = client.post(f"/api/projects/{pid}/plan").json()
    assert len(p["plan"]["transitions"]) == 1
    assert p["plan"]["transitions"][0]["overlap_beats"] != 12  # the A->B edit was about a pair that is gone


def test_editing_a_stale_plan_is_refused(client, planned):
    pid = planned["id"]
    client.patch(f"/api/projects/{pid}/settings", json={"style": "crossfade"})
    r = client.patch(f"/api/projects/{pid}/plan/transitions/0", json={"overlap_beats": 8})
    assert r.status_code == 409 and "out of date" in r.json()["detail"]


def test_downbeat_override_and_its_removal(client, planned):
    pid = planned["id"]
    t = planned["tracks"][0]
    auto = t["analysis"]["downbeat"]
    forced = (auto + 1) % 4
    p = client.put(f"/api/projects/{pid}/tracks/{t['id']}/downbeat", json={"downbeat": forced}).json()
    tv = p["tracks"][0]
    assert tv["downbeat_override"] == forced and tv["analysis"]["downbeat"] == forced
    assert tv["analysis"]["downbeat_manual"] and tv["analysis"]["downbeat_confidence"] == 1.0
    assert p["plan_stale"]
    p = client.post(f"/api/projects/{pid}/plan").json()
    assert p["plan"]["tracks"][0]["downbeat"] == forced
    p = client.put(f"/api/projects/{pid}/tracks/{t['id']}/downbeat", json={"downbeat": None}).json()
    assert p["tracks"][0]["analysis"]["downbeat"] == auto and p["tracks"][0]["downbeat_override"] is None
    assert client.put(f"/api/projects/{pid}/tracks/{t['id']}/downbeat", json={"downbeat": 7}).status_code == 422


def test_downbeat_override_survives_reanalysis(client, planned):
    pid = planned["id"]
    t = planned["tracks"][0]
    forced = (t["analysis"]["downbeat"] + 2) % 4
    client.put(f"/api/projects/{pid}/tracks/{t['id']}/downbeat", json={"downbeat": forced})
    j = client.post(f"/api/projects/{pid}/analyze", json={"force": True}).json()
    assert wait_job(client, j["id"])["state"] == "done"
    assert client.get(f"/api/projects/{pid}").json()["tracks"][0]["analysis"]["downbeat"] == forced


def test_a_changed_file_must_be_analysed_again(client, planned, tmp_path):
    pid = planned["id"]
    import shutil

    from synth import make_track

    p = tmp_path / "moving.wav"
    make_track(p, 120.0, 0.2, 55.0, seed=9)
    t = client.post(f"/api/projects/{pid}/tracks", json={"path": str(p)}).json()
    assert not t["analysed"]
    j = client.post(f"/api/projects/{pid}/analyze", json={"replan": False}).json()
    wait_job(client, j["id"])
    assert any(x["analysed"] for x in client.get(f"/api/projects/{pid}").json()["tracks"] if x["id"] == t["id"])
    shutil.copy(p, tmp_path / "bak.wav")
    make_track(p, 130.0, 0.2, 55.0, seed=9)  # same path, different audio
    view = client.get(f"/api/projects/{pid}").json()
    assert not next(x for x in view["tracks"] if x["id"] == t["id"])["analysed"]


# ---------- media endpoints ----------
def test_waveform_peaks(client, planned):
    pid, t = planned["id"], planned["tracks"][0]
    w = client.get(f"/api/projects/{pid}/tracks/{t['id']}/waveform", params={"points": 400}).json()
    assert w["points"] == 400 and len(w["min"]) == len(w["max"]) == 400
    assert all(lo <= hi for lo, hi in zip(w["min"], w["max"], strict=True))
    assert max(w["max"]) > 0.3 and min(w["min"]) < -0.3 and w["duration_s"] == pytest.approx(t["duration_s"])
    assert client.get(f"/api/projects/{pid}/tracks/{t['id']}/waveform", params={"points": 5}).status_code == 422
    again = client.get(f"/api/projects/{pid}/tracks/{t['id']}/waveform", params={"points": 400}).json()
    assert again == w  # served from the cache


def test_original_audio_supports_range_requests(client, planned):
    pid, t = planned["id"], planned["tracks"][0]
    r = client.get(f"/api/projects/{pid}/tracks/{t['id']}/audio", headers={"Range": "bytes=0-99"})
    assert r.status_code == 206 and len(r.content) == 100 and r.headers["content-type"] == "audio/wav"
    assert r.content[:4] == b"RIFF"


def test_click_clip_is_a_wav_of_the_requested_length(client, planned):
    pid, t = planned["id"], planned["tracks"][0]
    r = client.get(f"/api/projects/{pid}/tracks/{t['id']}/click", params={"seconds": 6})  # what an <audio> element does
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav"
    import io

    data, sr = sf.read(io.BytesIO(r.content))
    assert sr == SR and 5.9 < len(data) / sr < 6.1
    assert client.post(f"/api/projects/{pid}/tracks/{t['id']}/click", params={"seconds": 3}).status_code == 200


def test_click_needs_an_analysis(client, project):
    t = project["tracks"][0]
    assert client.get(f"/api/projects/{project['id']}/tracks/{t['id']}/click").status_code == 409


# ---------- jobs ----------
def test_unknown_jobs_are_404(client):
    assert client.get("/api/jobs/j_nope").status_code == 404
    assert client.delete("/api/jobs/j_nope").status_code == 404
    assert client.get("/api/jobs/j_nope/events").status_code == 404


def test_cancelling_a_running_job(client):
    import threading

    started = threading.Event()

    def slow(ctx):
        started.set()
        for _ in range(200):
            ctx.report("working")
            import time

            time.sleep(0.02)
        return {"finished": True}

    job = client.app.state.jobs.submit("demo", slow)
    assert started.wait(5)
    assert client.delete(f"/api/jobs/{job.id}").status_code == 200
    assert wait_job(client, job.id)["state"] == "cancelled"
    assert job.id in [j["id"] for j in client.get("/api/jobs").json()]


# ---------- rendering ----------
def test_rendering_without_rubberband_is_a_clear_503_and_planning_still_works(client, planned, monkeypatch):
    from blendscape.server import app as app_mod

    monkeypatch.setattr(app_mod, "available", lambda: False)
    pid = planned["id"]
    for url in (f"/api/projects/{pid}/render", f"/api/projects/{pid}/plan/transitions/0/preview"):
        r = client.post(url)
        assert r.status_code == 503 and "Rubber Band" in r.json()["detail"]
    assert client.post(f"/api/projects/{pid}/plan").status_code == 200


def test_rendering_needs_a_current_plan(client, project):
    pid = project["id"]
    assert client.post(f"/api/projects/{pid}/render").status_code in (409, 503)
    if client.get("/api/health").json()["stretch_backend"]:
        assert client.post(f"/api/projects/{pid}/render").status_code == 409


@pytest.mark.slow
@needs_rubberband
def test_preview_of_one_transition(client, planned):
    pid = planned["id"]
    tr = planned["plan"]["transitions"][0]
    j = client.post(f"/api/projects/{pid}/plan/transitions/0/preview", json={"lead_s": 4, "tail_s": 4})
    assert j.status_code == 202
    job = wait_job(client, j.json()["id"])
    assert job["state"] == "done", job
    res = job["result"]
    assert res["duration_s"] == pytest.approx(4 + tr["overlap_s"] + 4, abs=1.0)
    assert res["overlap_start_s"] == pytest.approx(4, abs=0.2)
    r = client.get(res["url"])
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav"
    import io

    data, sr = sf.read(io.BytesIO(r.content))
    assert sr == SR and len(data) / sr == pytest.approx(res["duration_s"], abs=0.05)
    assert abs(data).max() <= 10 ** (planned["settings"]["ceiling_db"] / 20) + 1e-3
    assert abs(data).max() > 0.05  # not silence
    assert client.get(f"/api/projects/{pid}/previews/..%2f..%2fproject.json").status_code == 404
    assert client.get(f"/api/projects/{pid}/previews/nope.wav").status_code == 404


@pytest.mark.slow
@needs_rubberband
def test_full_render_matches_the_library(client, planned):
    from blendscape.model import MixPlan
    from blendscape.render import render

    pid = planned["id"]
    j = client.post(f"/api/projects/{pid}/render", json={"name": "my set"})
    job = wait_job(client, j.json()["id"])
    assert job["state"] == "done", job
    res = job["result"]
    assert res["name"].startswith("my set-") and res["name"].endswith(".wav") and not res["clipped"]
    r = client.get(res["url"])
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    assert client.get(f"/api/projects/{pid}").json()["mixes"][0]["name"] == res["name"]
    plan = MixPlan.from_dict(client.get(f"/api/projects/{pid}/plan").json()["plan"])
    mix, report = render(plan)
    assert res["duration_s"] == pytest.approx(report.duration_s, abs=1e-6)
    assert res["peak_db"] == pytest.approx(report.peak_after_db, abs=0.01)
    info = sf.info(__import__("io").BytesIO(r.content))
    assert info.subtype == "PCM_24" and info.samplerate == SR
    fractions = [e.fraction for e in client.app.state.jobs.get(j.json()["id"]).events if e.fraction is not None]
    assert fractions == sorted(fractions)


def test_render_name_is_sanitised(client, planned):
    r = client.post(f"/api/projects/{planned['id']}/render", json={"name": "../../x"})
    assert r.status_code in (202, 503)  # '/' is stripped, the rest is a legal name
    if r.status_code == 202:
        client.delete(f"/api/jobs/{r.json()['id']}")
    assert client.post(f"/api/projects/{planned['id']}/render", json={"name": "///"}).status_code == 422


def test_web_ui_assets_have_correct_mime(tmp_path, monkeypatch):
    import mimetypes

    from fastapi.testclient import TestClient

    from blendscape.server.app import create_app

    web = tmp_path / "dist"
    (web / "assets").mkdir(parents=True)
    (web / "index.html").write_text("<html></html>")
    (web / "assets" / "a.js").write_text("export {}")
    monkeypatch.setattr(mimetypes, "guess_type", lambda *a, **k: ("text/plain", None))  # misconfigured Windows registry
    app = create_app(home=tmp_path / "home", web_dir=web)
    r = TestClient(app).get("/assets/a.js")
    assert r.headers["content-type"].startswith("text/javascript")
    assert r.headers["cache-control"] == "no-cache"
