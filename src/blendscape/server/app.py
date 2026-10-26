# SPDX-License-Identifier: GPL-3.0-or-later
"""The Blendscape HTTP API. See docs/API.md for the endpoint list; interactive docs are served at /docs.

Single-user and local: it binds to 127.0.0.1 by default and trusts whoever can reach it (it reads audio files from
paths you give it). Do not expose it to a network."""
from __future__ import annotations

import io
import json
import os
import random
import shutil
import time
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path

import numpy as np
import soundfile as sf
from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .. import __version__
from .. import config as appconfig
from .. import stretch as stretch_mod
from ..analysis import AnalysisError, TrackAnalysis, analyze, with_downbeat
from ..audio import AudioError, load_audio, write_audio
from ..check import click_check
from ..model import EQCurves, LimiterSettings, MixPlan, PlanError, check, validate
from ..planner import make_plan
from ..render import render, render_preview
from ..stretch import StretchError, available, backend
from ..summary import describe_plan
from .jobs import JobContext, JobManager
from .schemas import (
    AddTrackPath,
    AlternativeIn,
    AnalyzeIn,
    ConfigIn,
    DownbeatIn,
    OrderIn,
    PreviewIn,
    ProjectCreate,
    ProjectRename,
    RenderIn,
    Settings,
    SettingsPatch,
    ShuffleIn,
    TransitionPatch,
)
from .store import NotFound, ProjectStore, new_id, safe_filename

KEEP_PREVIEWS = 20
AUDIO_TYPES = {".wav": "audio/wav", ".flac": "audio/flac", ".ogg": "audio/ogg", ".aif": "audio/aiff",
               ".aiff": "audio/aiff"}


class Conflict(Exception):
    """The request is fine but the project is not in a state where it can be done (HTTP 409)."""


def _pair(tr_from: str, tr_to: str) -> str:
    return f"{tr_from}|{tr_to}"


# ======================================================================================================
# project logic (pure functions over the store, so they are easy to test)
# ======================================================================================================
def track_of(proj: dict, tid: str) -> dict:
    for t in proj["tracks"]:
        if t["id"] == tid:
            return t
    raise NotFound(tid)


def plan_of(proj: dict) -> MixPlan | None:
    return MixPlan.from_dict(proj["plan"]) if proj.get("plan") else None


def effective_analyses(store: ProjectStore, proj: dict) -> list[TrackAnalysis]:
    out = []
    for t in proj["tracks"]:
        try:
            key = store.file_key(t["path"])
        except OSError as e:
            raise Conflict(f"'{t['name']}' is no longer readable at {t['path']} ({e.strerror})") from e
        a = store.load_analysis(proj["id"], t["id"], key)
        if a is None:
            raise Conflict(f"'{t['name']}' has not been analysed yet")
        out.append(a)
    return out


def build_plan(store: ProjectStore, proj: dict) -> tuple[MixPlan, list[str]]:
    """A fresh plan for the project's current tracks and settings, with the user's manual edits re-applied wherever
    they still make sense. Returns the plan and notes about edits that had to be dropped."""
    if not proj["tracks"]:
        raise Conflict("add some tracks first")
    s = Settings(**proj["settings"])
    plan = make_plan(
        effective_analyses(store, proj), overlap_beats=s.overlap_beats, style=s.style, phrase_bars=s.phrase_bars,
        sample_rate=s.sample_rate, bit_depth=s.bit_depth, ceiling_db=s.ceiling_db, mix_points=s.mix_points,
        overlap_seconds=s.overlap_seconds, max_cut_s=s.max_cut_seconds, track_ids=[t["id"] for t in proj["tracks"]],
    )
    plan.limiter = LimiterSettings(**s.limiter.model_dump())
    notes: list[str] = []
    names = {t["id"]: t["name"] for t in proj["tracks"]}
    for tr in plan.transitions:
        ov = proj["overrides"].get(_pair(tr.from_track, tr.to_track))
        if not ov:
            continue
        before = (tr.out_beat, tr.in_beat, tr.overlap_beats, tr.style, tr.eq, tr.score, tr.note)
        apply_override(tr, ov)
        errs = validate(plan)
        if errs:
            tr.out_beat, tr.in_beat, tr.overlap_beats, tr.style, tr.eq, tr.score, tr.note = before
            notes.append(f"Your edit to {names[tr.from_track]} -> {names[tr.to_track]} no longer fits and was "
                         f"dropped ({errs[0]}).")
    return plan, notes


def apply_override(tr, ov: dict) -> None:
    for k in ("out_beat", "in_beat", "overlap_beats", "style"):
        if k in ov:
            setattr(tr, k, ov[k])
    if "eq" in ov:
        tr.eq = EQCurves(**{k: tuple(v) if isinstance(v, list) else v for k, v in ov["eq"].items()})
    tr.score, tr.note = None, "edited by hand"


def override_of(tr) -> dict:
    return {"out_beat": tr.out_beat, "in_beat": tr.in_beat, "overlap_beats": tr.overlap_beats, "style": tr.style,
            "eq": {"bass_swap_center": tr.eq.bass_swap_center, "bass_swap_beats": tr.eq.bass_swap_beats,
                   "mid_in": list(tr.eq.mid_in), "mid_out": list(tr.eq.mid_out), "hi_in": list(tr.eq.hi_in),
                   "hi_out": list(tr.eq.hi_out)}}


def replan(store: ProjectStore, pid: str) -> dict:
    proj = store.get(pid)
    plan, notes = build_plan(store, proj)

    def save(p: dict) -> None:
        p["plan"], p["plan_stale"], p["plan_warnings"] = plan.to_dict(), False, notes
        keep = {_pair(t.from_track, t.to_track) for t in plan.transitions}
        p["overrides"] = {k: v for k, v in p["overrides"].items() if k in keep}

    return store.update(pid, save)


def track_view(store: ProjectStore, proj: dict, t: dict) -> dict:
    a = None
    try:
        a = store.load_analysis(proj["id"], t["id"], store.file_key(t["path"]))
    except OSError:
        pass
    view = {k: t[k] for k in ("id", "name", "source", "duration_s", "samplerate", "channels", "downbeat_override")}
    view["analysed"] = a is not None
    view["missing"] = not os.path.exists(t["path"])
    if a:
        view["analysis"] = {
            "bpm": a.bpm, "offset_s": a.offset_s, "downbeat": a.downbeat, "downbeat_confidence": a.downbeat_confidence,
            "downbeat_probs": a.downbeat_probs, "downbeat_manual": a.downbeat_manual, "sections": a.sections,
            "active_end_s": a.active_end_s,
        }
    return view


def project_view(store: ProjectStore, proj: dict) -> dict:
    plan = plan_of(proj)
    desc = None
    if plan is not None:
        errs = validate(plan)
        names = {t["id"]: t["name"] for t in proj["tracks"]}  # what the user called them, not the stored file name
        desc = describe_plan(plan, names) if not errs else None
        for t in (desc or {}).get("transitions", []):
            t["edited"] = _pair(t["from"], t["to"]) in proj["overrides"]  # drives the "reset" button
    return {
        "id": proj["id"], "name": proj["name"], "created": proj["created"], "updated": proj["updated"],
        "settings": proj["settings"], "tracks": [track_view(store, proj, t) for t in proj["tracks"]],
        "plan": desc, "plan_stale": proj["plan_stale"], "plan_warnings": proj["plan_warnings"],
        "mixes": _files(store.dir(proj["id"]) / "mixes"),
    }


def _files(d: Path) -> list[dict]:
    if not d.exists():
        return []
    out = [{"name": f.name, "size": f.stat().st_size, "modified": f.stat().st_mtime}
           for f in d.iterdir() if f.is_file() and f.suffix == ".wav"]
    return sorted(out, key=lambda f: f["modified"], reverse=True)


def waveform_peaks(path: str, cache: Path, points: int) -> dict:
    f = cache / f"wave-{points}.json"
    key = f"{os.stat(path).st_size}-{int(os.stat(path).st_mtime)}"
    if f.exists():
        try:
            d = json.loads(f.read_text())
            if d.get("key") == key:
                return d
        except (OSError, ValueError):
            pass
    x, sr = sf.read(path, dtype="float32", always_2d=True)
    mono = x.mean(axis=1)
    edges = np.linspace(0, len(mono), points + 1).astype(int)
    lo = [round(float(mono[a:b].min()), 4) if b > a else 0.0 for a, b in zip(edges[:-1], edges[1:], strict=True)]
    hi = [round(float(mono[a:b].max()), 4) if b > a else 0.0 for a, b in zip(edges[:-1], edges[1:], strict=True)]
    d = {"key": key, "points": points, "min": lo, "max": hi, "duration_s": len(mono) / sr, "sample_rate": sr}
    cache.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(d))
    return d


def _check_audio(path: Path) -> sf._SoundFileInfo:
    try:
        info = sf.info(str(path))
    except (sf.LibsndfileError, RuntimeError, OSError) as e:
        raise AudioError(f"{path.name} is not an audio file this program can read ({e})") from e
    if info.frames == 0:
        raise AudioError(f"{path.name} contains no audio")
    return info


def _track_entry(path: Path, name: str, source: str, size: int) -> dict:
    info = _check_audio(path)
    return {"id": new_id("t"), "name": name, "path": str(path), "source": source, "size": size,
            "duration_s": info.frames / info.samplerate, "samplerate": info.samplerate, "channels": info.channels,
            "downbeat_override": None}


# ======================================================================================================
# the app
# ======================================================================================================
# the built web UI, copied in here when a release is built (scripts/bundle_ui.py)
PACKAGED_UI = Path(__file__).resolve().parents[1] / "webui"
SOURCE_UI = Path(__file__).resolve().parents[3] / "web" / "dist"  # only exists in a source checkout

# Windows takes MIME types from the registry, where .js is often text/plain, and browsers refuse to run module
# scripts served that way (blank page). So the UI's file types are pinned here rather than looked up.
_UI_TYPES = {".js": "text/javascript; charset=utf-8", ".mjs": "text/javascript; charset=utf-8",
             ".css": "text/css; charset=utf-8", ".map": "application/json", ".svg": "image/svg+xml",
             ".html": "text/html; charset=utf-8", ".json": "application/json"}


class _UIFiles(StaticFiles):
    def file_response(self, full_path, *args, **kwargs):
        resp = super().file_response(full_path, *args, **kwargs)
        mt = _UI_TYPES.get(Path(str(full_path)).suffix.lower())
        if mt:
            resp.headers["content-type"] = mt
        # always revalidate (cheap 304 via ETag): index.html isn't hashed, and a wrongly-typed copy must not stick
        resp.headers["cache-control"] = "no-cache"
        return resp


def create_app(home: Path | str | None = None, workers: int = 2, web_dir: Path | str | None = None) -> FastAPI:
    store = ProjectStore(home)
    stretch_mod.set_default_path(appconfig.load(store.home)["rubberband"])
    jobs = JobManager(workers)
    analysis_cache = store.home / "analysis-cache"

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        jobs.shutdown()

    app = FastAPI(title="Blendscape", version=__version__, lifespan=lifespan,
                  description="Automatic DJ-style blending of an ordered list of audio files. Local use only.")
    app.state.store, app.state.jobs = store, jobs
    app.add_middleware(
        CORSMiddleware, allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
        allow_methods=["*"], allow_headers=["*"],
    )

    # ---------- errors ----------
    @app.exception_handler(NotFound)
    async def _nf(_r: Request, e: NotFound):
        return JSONResponse({"detail": f"not found: {e.args[0]}"}, status_code=404)

    @app.exception_handler(Conflict)
    async def _conflict(_r: Request, e: Conflict):
        return JSONResponse({"detail": str(e)}, status_code=409)

    @app.exception_handler(PlanError)
    async def _plan_err(_r: Request, e: PlanError):
        errors = [x.strip("- ").strip() for x in str(e).splitlines()[1:] if x.strip()] if "\n" in str(e) else [str(e)]
        return JSONResponse({"detail": errors[0] if errors else str(e), "errors": errors}, status_code=422)

    @app.exception_handler(AnalysisError)
    @app.exception_handler(AudioError)
    async def _input_err(_r: Request, e: Exception):
        return JSONResponse({"detail": str(e)}, status_code=422)

    def need_render_backend() -> None:
        if not available():
            raise HTTPException(503, "Rubber Band is not installed: put the `rubberband` executable on PATH "
                                     "(or set RUBBERBAND=path) and restart.")

    # ---------- health ----------
    @app.get("/api/health", tags=["system"])
    def health():
        try:
            stretch = backend()
        except StretchError:
            stretch = None
        return {"ok": True, "version": __version__, "stretch_backend": stretch, "home": str(store.home)}

    # ---------- per-computer settings ----------
    def _config_view() -> dict:
        cfg = appconfig.load(store.home)
        return {"rubberband": cfg["rubberband"], "rubberband_in_use": stretch_mod._cli_path(),
                "rubberband_version": stretch_mod.cli_version(), "export_dir": cfg["export_dir"],
                "ui_bundled": PACKAGED_UI.is_dir()}

    @app.get("/api/config", tags=["system"])
    def get_config():
        """Where Rubber Band is and where finished mixes are copied (both optional, per computer)."""
        return _config_view()

    @app.put("/api/config", tags=["system"])
    def put_config(body: ConfigIn):
        changes: dict[str, str | None] = {}
        if "rubberband" in body.model_fields_set:
            rb = (body.rubberband or "").strip().strip('"')
            if rb:
                path = Path(rb).expanduser()
                if path.is_dir():  # be forgiving: a folder that holds it
                    path = next((c for c in (path / "rubberband.exe", path / "rubberband") if c.is_file()), path)
                if not path.is_file():
                    raise HTTPException(422, f"no such file: {rb}")
                rb = str(path)
            changes["rubberband"] = rb or None
        if "export_dir" in body.model_fields_set:
            ed = (body.export_dir or "").strip().strip('"')
            if ed:
                d = Path(ed).expanduser()
                try:
                    d.mkdir(parents=True, exist_ok=True)
                except OSError as e:
                    raise HTTPException(422, f"can't use that folder: {e}") from e
                ed = str(d)
            changes["export_dir"] = ed or None
        cfg = appconfig.save(store.home, **changes)
        stretch_mod.set_default_path(cfg["rubberband"])
        return _config_view()

    # ---------- projects ----------
    @app.get("/api/projects", tags=["projects"])
    def list_projects():
        return store.list()

    @app.post("/api/projects", status_code=201, tags=["projects"])
    def create_project(body: ProjectCreate):
        return project_view(store, store.create(body.name, body.settings))

    @app.get("/api/projects/{pid}", tags=["projects"])
    def get_project(pid: str):
        return project_view(store, store.get(pid))

    @app.patch("/api/projects/{pid}", tags=["projects"])
    def rename_project(pid: str, body: ProjectRename):
        return project_view(store, store.update(pid, lambda p: p.update(name=body.name)))

    @app.delete("/api/projects/{pid}", status_code=204, tags=["projects"])
    def delete_project(pid: str):
        store.delete(pid)

    @app.patch("/api/projects/{pid}/settings", tags=["projects"])
    def patch_settings(pid: str, body: SettingsPatch):
        changes = body.model_dump(exclude_unset=True)

        def apply(p: dict) -> None:
            merged = dict(p["settings"])
            clear = changes.pop("clear_overlap_seconds", False)
            for k, v in changes.items():
                merged[k] = v
            if clear:
                merged["overlap_seconds"] = None
            p["settings"] = Settings(**merged).model_dump()  # validates the combination
            if p["plan"]:
                p["plan_stale"] = True

        return project_view(store, store.update(pid, apply))

    # ---------- tracks ----------
    @app.post("/api/projects/{pid}/tracks", status_code=201, tags=["tracks"])
    def add_track_by_path(pid: str, body: AddTrackPath):
        """Add a file that already lives on this computer; it is read in place and never copied."""
        store.dir(pid)
        path = Path(body.path).expanduser()
        if not path.is_file():
            raise HTTPException(422, f"no such file: {body.path}")
        entry = _track_entry(path.resolve(), path.name, "path", path.stat().st_size)
        proj = store.update(pid, lambda p: (p["tracks"].append(entry), _stale(p)))
        return track_view(store, proj, entry)

    @app.post("/api/projects/{pid}/tracks/upload", status_code=201, tags=["tracks"])
    def add_track_upload(pid: str, file: UploadFile = File(...)):
        """Add a file by uploading it (what a browser does); a copy is kept inside the project."""
        d = store.dir(pid)
        name = safe_filename(file.filename or "track")
        tid_dest = d / "audio" / f"{new_id('u')}-{name}"
        size = 0
        with open(tid_dest, "wb") as out:
            while chunk := file.file.read(1 << 20):
                out.write(chunk)
                size += len(chunk)
        try:
            entry = _track_entry(tid_dest, name, "upload", size)
        except AudioError:
            tid_dest.unlink(missing_ok=True)
            raise
        proj = store.update(pid, lambda p: (p["tracks"].append(entry), _stale(p)))
        return track_view(store, proj, entry)

    @app.delete("/api/projects/{pid}/tracks/{tid}", status_code=204, tags=["tracks"])
    def delete_track(pid: str, tid: str):
        t = track_of(store.get(pid), tid)

        def apply(p: dict) -> None:
            p["tracks"] = [x for x in p["tracks"] if x["id"] != tid]
            p["overrides"] = {k: v for k, v in p["overrides"].items() if tid not in k.split("|")}
            _stale(p)

        store.update(pid, apply)
        store.drop_analysis(pid, tid)
        if t["source"] == "upload":
            Path(t["path"]).unlink(missing_ok=True)

    @app.put("/api/projects/{pid}/tracks/order", tags=["tracks"])
    def reorder(pid: str, body: OrderIn):
        def apply(p: dict) -> None:
            have = {t["id"] for t in p["tracks"]}
            if sorted(body.order) != sorted(have):
                raise HTTPException(422, "order must list every track id exactly once")
            by_id = {t["id"]: t for t in p["tracks"]}
            p["tracks"] = [by_id[i] for i in body.order]
            _stale(p)

        return project_view(store, store.update(pid, apply))

    @app.post("/api/projects/{pid}/tracks/shuffle", tags=["tracks"])
    def shuffle(pid: str, body: ShuffleIn | None = None):
        """Put the tracks in a random (different) order and, if they are all analysed, plan the transitions again.
        Edits you made to a transition survive only if that same pair of songs is still next to each other."""
        proj = store.get(pid)
        ids = [t["id"] for t in proj["tracks"]]
        if len(ids) < 2:
            raise Conflict("add at least two tracks to shuffle")
        rng = random.Random(body.seed if body else None)
        order = ids[:]
        for _ in range(50):
            rng.shuffle(order)
            if order != ids:
                break
        else:  # (only for seeds that keep landing on the same order) rotate: always different with 2+ distinct ids
            order = ids[1:] + ids[:1]

        def apply(p: dict) -> None:
            by_id = {t["id"]: t for t in p["tracks"]}
            p["tracks"] = [by_id[i] for i in order]
            _stale(p)

        updated = store.update(pid, apply)
        analysed = all(store.load_analysis(pid, t["id"], store.file_key(t["path"])) is not None
                       for t in updated["tracks"])
        return project_view(store, replan(store, pid) if analysed else updated)

    @app.put("/api/projects/{pid}/tracks/{tid}/downbeat", tags=["tracks"])
    def set_downbeat(pid: str, tid: str, body: DownbeatIn):
        """Force which grid beat (0-3) starts a bar, or null to go back to the detected one."""
        proj = store.get(pid)
        t = track_of(proj, tid)
        key = store.file_key(t["path"])
        auto = store.load_analysis(pid, tid, key, auto=True)
        if auto is None:
            raise Conflict("analyse the project first")
        eff = with_downbeat(auto, body.downbeat) if body.downbeat is not None else auto
        store.save_analysis(pid, tid, key, eff, auto=False)
        proj = store.update(pid, lambda p: (track_of(p, tid).update(downbeat_override=body.downbeat), _stale(p)))
        return project_view(store, proj)

    @app.get("/api/projects/{pid}/tracks/{tid}/analysis", tags=["tracks"])
    def get_analysis(pid: str, tid: str):
        proj = store.get(pid)
        t = track_of(proj, tid)
        a = store.load_analysis(pid, tid, store.file_key(t["path"]))
        if a is None:
            raise Conflict("not analysed yet")
        return asdict(a)

    @app.get("/api/projects/{pid}/tracks/{tid}/waveform", tags=["tracks"])
    def get_waveform(pid: str, tid: str, points: int = Query(1500, ge=100, le=20000)):
        proj = store.get(pid)
        t = track_of(proj, tid)
        if not os.path.exists(t["path"]):
            raise Conflict(f"{t['path']} is missing")
        return waveform_peaks(t["path"], store.dir(pid) / "cache" / tid_safe(tid), points)

    @app.get("/api/projects/{pid}/tracks/{tid}/audio", tags=["tracks"])
    def get_audio(pid: str, tid: str):
        """The original file (supports Range requests, so a browser can seek)."""
        t = track_of(store.get(pid), tid)
        if not os.path.exists(t["path"]):
            raise Conflict(f"{t['path']} is missing")
        return FileResponse(t["path"], media_type=AUDIO_TYPES.get(Path(t["path"]).suffix.lower(), "audio/wav"))

    @app.api_route("/api/projects/{pid}/tracks/{tid}/click", methods=["GET", "POST"], tags=["tracks"])
    def click_clip(pid: str, tid: str, start: float | None = Query(None, ge=0),
                   seconds: float = Query(30, gt=0, le=120)):
        """A short WAV with a metronome on the detected bar line, to check the downbeat by ear. GET so an audio
        element can play it directly (POST is accepted too)."""
        proj = store.get(pid)
        t = track_of(proj, tid)
        a = store.load_analysis(pid, tid, store.file_key(t["path"]))
        if a is None:
            raise Conflict("analyse the project first")
        audio, _ = load_audio(t["path"])
        mix, _start = click_check(a, audio, start, seconds)
        buf = io.BytesIO()
        sf.write(buf, mix, a.sample_rate, format="WAV", subtype="PCM_16")
        return Response(buf.getvalue(), media_type="audio/wav", headers={"Cache-Control": "no-store"})

    # ---------- analysis and planning ----------
    @app.post("/api/projects/{pid}/analyze", status_code=202, tags=["planning"])
    def start_analysis(pid: str, body: AnalyzeIn | None = None):
        body = body or AnalyzeIn()
        proj = store.get(pid)
        if not proj["tracks"]:
            raise Conflict("add some tracks first")

        def work(ctx: JobContext) -> dict:
            tracks = store.get(pid)["tracks"]
            for i, t in enumerate(tracks):
                ctx.report(f"analysing {t['name']} ({i + 1}/{len(tracks)})", i / len(tracks))
                key = store.file_key(t["path"])
                if body.force or store.load_analysis(pid, t["id"], key, auto=True) is None:
                    a = analyze(t["path"], cache_dir=analysis_cache, use_cache=not body.force)
                    store.save_analysis(pid, t["id"], key, a, auto=True)
                    eff = a
                    if t["downbeat_override"] is not None:
                        eff = with_downbeat(a, t["downbeat_override"])
                    store.save_analysis(pid, t["id"], key, eff, auto=False)
            result = {"tracks": len(tracks), "planned": False}
            if body.replan:
                ctx.report("planning", 0.95)
                replan(store, pid)
                result["planned"] = True
            return result

        return jobs.submit("analyze", work, pid).to_dict()

    @app.post("/api/projects/{pid}/plan", tags=["planning"])
    def make_new_plan(pid: str):
        """Plan again from the current settings. Edits you made to individual transitions are kept where they still
        fit. Needs every track analysed."""
        return project_view(store, replan(store, pid))

    @app.get("/api/projects/{pid}/plan", tags=["planning"])
    def get_plan(pid: str):
        proj = store.get(pid)
        if not proj["plan"]:
            raise Conflict("no plan yet: analyse and plan first")
        return {"plan": proj["plan"], "stale": proj["plan_stale"], "warnings": proj["plan_warnings"],
                "description": project_view(store, proj)["plan"]}

    def _edit_transition(pid: str, index: int, edit) -> dict:
        proj = store.get(pid)
        plan = plan_of(proj)
        if plan is None:
            raise Conflict("no plan yet")
        if proj["plan_stale"]:
            raise Conflict("the plan is out of date: plan again first")
        if not 0 <= index < len(plan.transitions):
            raise NotFound(f"transition {index}")
        tr = plan.transitions[index]
        edit(tr)
        errs = validate(plan)
        if errs:
            raise PlanError("invalid plan:\n  - " + "\n  - ".join(errs))
        key = _pair(tr.from_track, tr.to_track)

        def save(p: dict) -> None:
            p["plan"] = plan.to_dict()
            p["overrides"][key] = override_of(tr)

        return project_view(store, store.update(pid, save))

    @app.patch("/api/projects/{pid}/plan/transitions/{index}", tags=["planning"])
    def patch_transition(pid: str, index: int, body: TransitionPatch):
        changes = body.model_dump(exclude_unset=True)

        def edit(tr) -> None:
            eq = changes.pop("eq", None)
            for k, v in changes.items():
                setattr(tr, k, v)
            if eq:
                for k, v in eq.items():
                    setattr(tr.eq, k, tuple(v) if isinstance(v, list | tuple) else v)
            tr.score, tr.note = None, "edited by hand"

        return _edit_transition(pid, index, edit)

    @app.post("/api/projects/{pid}/plan/transitions/{index}/alternative", tags=["planning"])
    def use_alternative(pid: str, index: int, body: AlternativeIn):
        def edit(tr) -> None:
            if body.index >= len(tr.alternatives):
                raise HTTPException(422, f"this transition has {len(tr.alternatives)} alternatives")
            alt = tr.alternatives[body.index]
            tr.out_beat, tr.in_beat = alt["out_beat"], alt["in_beat"]
            tr.score, tr.note = alt.get("score"), (alt.get("note") or "") + " (chosen from the alternatives)"

        return _edit_transition(pid, index, edit)

    @app.delete("/api/projects/{pid}/plan/transitions/{index}/override", tags=["planning"])
    def reset_transition(pid: str, index: int):
        """Forget your edits to this transition and let the planner choose again."""
        proj = store.get(pid)
        plan = plan_of(proj)
        if plan is None or not 0 <= index < len(plan.transitions):
            raise NotFound(f"transition {index}")
        tr = plan.transitions[index]
        store.update(pid, lambda p: p["overrides"].pop(_pair(tr.from_track, tr.to_track), None))
        return project_view(store, replan(store, pid))

    # ---------- rendering ----------
    def _ready_plan(pid: str) -> tuple[dict, MixPlan]:
        proj = store.get(pid)
        plan = plan_of(proj)
        if plan is None:
            raise Conflict("no plan yet: analyse and plan first")
        if proj["plan_stale"]:
            raise Conflict("the plan is out of date: plan again first")
        check(plan)
        return proj, plan

    @app.post("/api/projects/{pid}/plan/transitions/{index}/preview", status_code=202, tags=["rendering"])
    def start_preview(pid: str, index: int, body: PreviewIn | None = None):
        """Render just this transition (with a few seconds either side) to a WAV you can play in the browser."""
        body = body or PreviewIn()
        need_render_backend()
        _proj, plan = _ready_plan(pid)
        if not 0 <= index < len(plan.transitions):
            raise NotFound(f"transition {index}")

        def work(ctx: JobContext) -> dict:
            steps = {"n": 0}

            def progress(msg: str) -> None:
                steps["n"] += 1 if "loading" in msg else 0
                ctx.report(msg, 0.85 if msg == "limiting" else 0.1 + 0.35 * steps["n"])

            audio, sr, report = render_preview(plan, index, body.lead_s, body.tail_s, progress=progress)
            ctx.report("writing the file", 0.95)
            d = store.dir(pid) / "previews"
            fname = f"transition-{index + 1}-{int(time.time())}.wav"
            write_audio(d / fname, audio, sr, 16)
            for old in sorted(d.glob("*.wav"), key=lambda f: f.stat().st_mtime)[:-KEEP_PREVIEWS]:
                old.unlink(missing_ok=True)
            return {"url": f"/api/projects/{pid}/previews/{fname}", "duration_s": report.duration_s,
                    "overlap_start_s": report.transitions[0][0], "overlap_end_s": report.transitions[0][1],
                    "peak_db": report.peak_after_db, "max_reduction_db": report.max_reduction_db}

        return jobs.submit("preview", work, pid).to_dict()

    @app.post("/api/projects/{pid}/render", status_code=202, tags=["rendering"])
    def start_render(pid: str, body: RenderIn | None = None):
        """Render the whole mix to a WAV file in the project's mixes folder."""
        body = body or RenderIn()
        need_render_backend()
        proj, plan = _ready_plan(pid)

        def work(ctx: JobContext) -> dict:
            n = len(plan.tracks)
            seen = {"i": 0}

            def progress(msg: str) -> None:
                if "loading" in msg:
                    seen["i"] += 1
                ctx.report(msg, min(0.9, seen["i"] / (n + 1)))

            mix, report = render(plan, progress=progress)
            ctx.report("writing the file", 0.95)
            fname = f"{body.name or safe_filename(proj['name'])}-{time.strftime('%Y%m%d-%H%M%S')}.wav"
            out = store.dir(pid) / "mixes" / fname
            write_audio(out, mix, plan.sample_rate, plan.bit_depth)
            result = {"url": f"/api/projects/{pid}/mixes/{fname}", "name": fname, "duration_s": report.duration_s,
                      "peak_db": report.peak_after_db, "max_reduction_db": report.max_reduction_db,
                      "limited_fraction": report.limited_fraction, "clipped": report.clipped}
            export_dir = appconfig.load(store.home)["export_dir"]
            if export_dir:  # a failed copy must not lose the render
                try:
                    Path(export_dir).mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(out, Path(export_dir) / fname)
                    result["copied_to"] = str(Path(export_dir) / fname)
                except OSError as e:
                    result["copy_error"] = f"couldn't copy to {export_dir}: {e}"
            return result

        return jobs.submit("render", work, pid).to_dict()

    def _served_file(pid: str, sub: str, name: str, download: bool) -> FileResponse:
        if name != safe_filename(name) or not name.endswith(".wav"):
            raise NotFound(name)
        f = store.dir(pid) / sub / name
        if not f.is_file():
            raise NotFound(name)
        return FileResponse(f, media_type="audio/wav", filename=name if download else None)

    @app.get("/api/projects/{pid}/previews/{name}", tags=["rendering"])
    def get_preview(pid: str, name: str):
        return _served_file(pid, "previews", name, download=False)

    @app.get("/api/projects/{pid}/mixes/{name}", tags=["rendering"])
    def get_mix(pid: str, name: str):
        return _served_file(pid, "mixes", name, download=True)

    # ---------- jobs ----------
    @app.get("/api/jobs", tags=["jobs"])
    def list_jobs(project: str | None = None):
        return [j.to_dict() for j in jobs.list(project)]

    @app.get("/api/jobs/{job_id}", tags=["jobs"])
    def get_job(job_id: str):
        j = jobs.get(job_id)
        if j is None:
            raise NotFound(job_id)
        return j.to_dict()

    @app.delete("/api/jobs/{job_id}", tags=["jobs"])
    def cancel_job(job_id: str):
        """Ask a job to stop. It stops at its next progress step, not instantly."""
        j = jobs.cancel(job_id)
        if j is None:
            raise NotFound(job_id)
        return j.to_dict()

    @app.get("/api/jobs/{job_id}/events", tags=["jobs"])
    def job_events(job_id: str):
        """Server-sent events: one `progress` event per step, ending with the final state (done / error /
        cancelled). Replays past events first, so it is safe to connect late."""
        if jobs.get(job_id) is None:
            raise NotFound(job_id)

        def gen():
            for ev in jobs.stream(job_id):
                if ev is None:
                    yield ": keep-alive\n\n"
                    continue
                job = jobs.get(job_id)
                payload = {"seq": ev.seq, "state": ev.state, "message": ev.message, "fraction": ev.fraction}
                if ev.state in ("done", "error", "cancelled") and job is not None:
                    payload.update(result=job.result, error=job.error)
                yield f"id: {ev.seq}\nevent: progress\ndata: {json.dumps(payload)}\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # ---------- web UI (built in milestone 4) ----------
    # explicit argument, then $BLENDSCAPE_WEB, then a source checkout's own build (so rebuilding while developing
    # wins over a stale bundled copy), then the UI shipped inside the installed package
    web = Path(web_dir or os.environ.get("BLENDSCAPE_WEB") or (SOURCE_UI if SOURCE_UI.is_dir() else PACKAGED_UI))
    if web.is_dir():
        app.mount("/", _UIFiles(directory=web, html=True), name="web")
    else:
        @app.get("/", include_in_schema=False)
        def index():
            return {"name": "Blendscape", "version": __version__, "docs": "/docs", "web_ui": "not built yet"}

    return app


def _stale(p: dict) -> None:
    if p["plan"]:
        p["plan_stale"] = True


def tid_safe(tid: str) -> str:
    return "".join(c for c in tid if c.isalnum() or c == "_")
