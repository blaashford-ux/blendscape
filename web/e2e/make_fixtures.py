# SPDX-License-Identifier: GPL-3.0-or-later
"""Write three short synthetic dance tracks for the end-to-end tests: python make_fixtures.py OUTDIR"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "tests"))
import synth  # noqa: E402

out = pathlib.Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
TRACKS = [("one", 124.0, 0.37, 55.0), ("two", 128.0, 0.81, 49.0), ("three", 126.0, 0.12, 58.27)]
for i, (name, bpm, off, key) in enumerate(TRACKS):
    f = out / f"{name}.wav"
    if not f.exists():
        synth.make_track(f, bpm, off, key, seed=i + 1)
(out / "notes.txt").write_text("this is not audio")
