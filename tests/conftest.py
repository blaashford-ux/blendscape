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
