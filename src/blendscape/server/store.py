# SPDX-License-Identifier: GPL-3.0-or-later
"""Projects on disk: one folder per project under the data home.

    <home>/projects/<id>/project.json     settings, track list, current plan, manual edits
    <home>/projects/<id>/audio/           copies of uploaded files
    <home>/projects/<id>/analysis/        <track>.auto.json (detected) and <track>.json (with any downbeat override)
    <home>/projects/<id>/previews/        transition previews
    <home>/projects/<id>/mixes/           rendered mixes
    <home>/projects/<id>/cache/           waveform peaks

Every change goes through :meth:`ProjectStore.update` which holds the project's lock and rewrites ``project.json``
atomically, so a crash never leaves half a file."""
from __future__ import annotations

import json
import os
import shutil
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path

from ..analysis import ANALYSIS_VERSION, TrackAnalysis
from .schemas import Settings


class NotFound(KeyError):
    pass


def default_home() -> Path:
    env = os.environ.get("BLENDSCAPE_HOME")
    if env:
        return Path(env)
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return Path(base) / "blendscape"


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def safe_filename(name: str) -> str:
    base = os.path.basename(name.replace("\\", "/"))
    cleaned = "".join(c if (c.isalnum() or c in " ._-()") else "_" for c in base).strip(" .")
    return cleaned or "track"


class ProjectStore:
    def __init__(self, home: Path | str | None = None):
        self.home = Path(home) if home else default_home()
        self.root = self.home / "projects"
        self.root.mkdir(parents=True, exist_ok=True)
        self._locks: dict[str, threading.RLock] = {}
        self._guard = threading.Lock()

    # ---------- paths ----------
    def dir(self, pid: str) -> Path:
        if not pid.startswith("p_") or not pid[2:].isalnum():
            raise NotFound(pid)
        d = self.root / pid
        if not (d / "project.json").exists():
            raise NotFound(pid)
        return d

    def lock(self, pid: str) -> threading.RLock:
        with self._guard:
            return self._locks.setdefault(pid, threading.RLock())

    # ---------- projects ----------
    def create(self, name: str, settings: Settings | None = None) -> dict:
        pid = new_id("p")
        d = self.root / pid
        for sub in ("audio", "analysis", "previews", "mixes", "cache"):
            (d / sub).mkdir(parents=True)
        now = time.time()
        proj = {"id": pid, "name": name, "created": now, "updated": now,
                "settings": (settings or Settings()).model_dump(), "tracks": [], "plan": None,
                "plan_stale": False, "plan_warnings": [], "overrides": {}}
        self._write(d, proj)
        return proj

    def get(self, pid: str) -> dict:
        d = self.dir(pid)
        with self.lock(pid):
            return json.loads((d / "project.json").read_text(encoding="utf-8"))

    def list(self) -> list[dict]:
        out = []
        for d in sorted(self.root.iterdir()):
            f = d / "project.json"
            if f.exists():
                try:
                    p = json.loads(f.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                out.append({"id": p["id"], "name": p["name"], "created": p["created"], "updated": p["updated"],
                            "tracks": len(p["tracks"])})
        return sorted(out, key=lambda p: p["updated"], reverse=True)

    def update(self, pid: str, fn: Callable[[dict], object]) -> dict:
        """Apply ``fn`` to the project dict under its lock and save. Whatever ``fn`` raises leaves the file as it
        was."""
        d = self.dir(pid)
        with self.lock(pid):
            proj = json.loads((d / "project.json").read_text(encoding="utf-8"))
            fn(proj)
            proj["updated"] = time.time()
            self._write(d, proj)
            return proj

    def delete(self, pid: str) -> None:
        d = self.dir(pid)
        with self.lock(pid):
            shutil.rmtree(d)

    @staticmethod
    def _write(d: Path, proj: dict) -> None:
        tmp = d / "project.json.tmp"
        tmp.write_text(json.dumps(proj, indent=2), encoding="utf-8")
        os.replace(tmp, d / "project.json")

    # ---------- analyses ----------
    def _afile(self, pid: str, tid: str, auto: bool) -> Path:
        return self.dir(pid) / "analysis" / f"{tid}{'.auto' if auto else ''}.json"

    @staticmethod
    def file_key(path: str) -> str:
        st = os.stat(path)
        return f"{st.st_size}-{int(st.st_mtime)}-v{ANALYSIS_VERSION}"

    def save_analysis(self, pid: str, tid: str, key: str, a: TrackAnalysis, auto: bool) -> None:
        f = self._afile(pid, tid, auto)
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps({"key": key, "analysis": asdict(a)}), encoding="utf-8")
        os.replace(tmp, f)

    def load_analysis(self, pid: str, tid: str, key: str | None = None, auto: bool = False) -> TrackAnalysis | None:
        """The stored analysis, or ``None`` if there is none or it is for a different file / analyser version."""
        f = self._afile(pid, tid, auto)
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if key is not None and d.get("key") != key:
            return None
        try:
            return TrackAnalysis(**d["analysis"])
        except TypeError:
            return None

    def drop_analysis(self, pid: str, tid: str) -> None:
        for auto in (True, False):
            self._afile(pid, tid, auto).unlink(missing_ok=True)
