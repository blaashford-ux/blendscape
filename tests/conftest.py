# SPDX-License-Identifier: GPL-3.0-or-later
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import synth  # noqa: E402
from blendscape import stretch  # noqa: E402

needs_rubberband = pytest.mark.skipif(not stretch.available(), reason="rubberband executable not found")

# (bpm, first-beat offset s, bass key Hz)
TRACKS = {"a": (124.0, 0.37, 55.0), "b": (128.0, 0.81, 49.0), "c": (126.0, 0.12, 58.27)}


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("BLENDSCAPE_CACHE", str(tmp_path / "cache"))


@pytest.fixture(scope="session")
def tracks(tmp_path_factory):
    """Three short synthetic dance tracks with known tempo/offset. Returns {name: (path, bpm, offset)}."""
    d = tmp_path_factory.mktemp("synth")
    out = {}
    for i, (name, (bpm, off, key)) in enumerate(TRACKS.items()):
        p = d / f"{name}_{int(bpm)}.wav"
        synth.make_track(p, bpm, off, key, seed=i + 1)
        out[name] = (str(p), bpm, off)
    return out


def pytest_collection_modifyitems(config, items):
    if os.environ.get("BLENDSCAPE_SKIP_SLOW"):
        skip = pytest.mark.skip(reason="BLENDSCAPE_SKIP_SLOW set")
        for item in items:
            if "slow" in item.keywords:
                item.add_marker(skip)


@pytest.fixture(scope="session")
def rich_set(tmp_path_factory):
    """Two tracks of every synthetic style (incl. the hard ones) with a known bar line: list of synth_rich.Truth."""
    import synth_rich

    d = tmp_path_factory.mktemp("rich")
    styles = synth_rich.STYLES + synth_rich.HARD_STYLES
    return synth_rich.make_set(str(d), per_style=2, seed=3, n_bars=32, styles=styles)


# ---------- API fixtures ----------
@pytest.fixture(scope="session")
def api_home(tmp_path_factory):
    """One data home for the whole session, so analyses (cached by content) are computed once."""
    return tmp_path_factory.mktemp("api-home")


@pytest.fixture
def client(api_home):
    from fastapi.testclient import TestClient

    from blendscape.server.app import create_app

    with TestClient(create_app(home=api_home, workers=2)) as c:
        yield c


def wait_job(client, job_id, timeout=240):
    import time

    t = time.time()
    while True:
        j = client.get(f"/api/jobs/{job_id}").json()
        if j["state"] in ("done", "error", "cancelled"):
            return j
        assert time.time() - t < timeout, f"job stuck: {j}"
        time.sleep(0.1)


@pytest.fixture
def project(client, tracks):
    """A fresh project holding the three synthetic tracks (by path), not yet analysed."""
    p = client.post("/api/projects", json={"name": "test mix"}).json()
    for name in "abc":
        r = client.post(f"/api/projects/{p['id']}/tracks", json={"path": tracks[name][0]})
        assert r.status_code == 201, r.text
    return client.get(f"/api/projects/{p['id']}").json()


@pytest.fixture
def planned(client, project):
    """The same project analysed and planned with a short overlap (so the 24-bar test tracks have room)."""
    pid = project["id"]
    assert client.patch(f"/api/projects/{pid}/settings", json={"overlap_beats": 16}).status_code == 200
    j = client.post(f"/api/projects/{pid}/analyze").json()
    assert wait_job(client, j["id"])["state"] == "done"
    return client.get(f"/api/projects/{pid}").json()
