# SPDX-License-Identifier: GPL-3.0-or-later
"""Per-computer settings that outlive a project: where Rubber Band is, and a folder to copy finished mixes to.

Kept in ``<home>/config.json`` next to the projects, so ``--home`` / ``BLENDSCAPE_HOME`` move it together."""
from __future__ import annotations

import json
import os
from pathlib import Path

KEYS = ("rubberband", "export_dir")


def _file(home: Path | str) -> Path:
    return Path(home) / "config.json"


def load(home: Path | str) -> dict[str, str | None]:
    try:
        data = json.loads(_file(home).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    return {k: (data.get(k) or None) if isinstance(data, dict) else None for k in KEYS}


def save(home: Path | str, **changes: str | None) -> dict[str, str | None]:
    """Change some keys (None clears one) and write the file atomically."""
    cur = load(home)
    for k, v in changes.items():
        if k not in KEYS:
            raise KeyError(k)
        cur[k] = v or None
    f = _file(home)
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(cur, indent=2), encoding="utf-8")
    os.replace(tmp, f)
    return cur
