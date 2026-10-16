#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Copy the built web UI (web/dist) into the Python package so a wheel carries it.

    cd web && npm ci && npm run build && cd ..
    python scripts/bundle_ui.py
    python -m build --wheel

The copy (src/blendscape/webui) is not committed; `blendscape serve` prefers it over web/dist."""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "web" / "dist"
DST = ROOT / "src" / "blendscape" / "webui"


def main() -> int:
    if not (SRC / "index.html").is_file():
        print(f"error: {SRC} is not built: run `npm ci && npm run build` in web/ first", file=sys.stderr)
        return 1
    if DST.exists():
        shutil.rmtree(DST)
    shutil.copytree(SRC, DST, ignore=shutil.ignore_patterns("*.map"))  # source maps are for developers
    n = sum(1 for f in DST.rglob("*") if f.is_file())
    print(f"copied {n} files from {SRC} to {DST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
