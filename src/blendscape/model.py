# SPDX-License-Identifier: GPL-3.0-or-later
"""The mix plan: pure data describing an ordered mix. Hand-editable JSON, no audio inside.

Beat numbers refer to each *source* track's own beat grid (beat k at ``offset_s + k * 60 / bpm``),
so a plan keeps its meaning when other transitions change."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .grid import BEATS_PER_BAR, BeatGrid

PLAN_VERSION = 1
STYLES = ("bass_swap", "crossfade")
BIT_DEPTHS = (16, 24, 32)


class PlanError(ValueError):
    """The plan is inconsistent; the message lists every problem found."""


@dataclass
class EQCurves:
    """When each band fades, as fractions (0..1) of the overlap, for the bass_swap style."""

    bass_swap_center: float = 0.5  # bass leaves A / enters B around this point
    bass_swap_beats: float = 4.0  # ... over this many beats
    mid_in: tuple[float, float] = (0.15, 0.60)
    mid_out: tuple[float, float] = (0.45, 0.85)
    hi_in: tuple[float, float] = (0.0, 0.40)
    hi_out: tuple[float, float] = (0.60, 1.0)


@dataclass
class LimiterSettings:
    """Final-mix peak limiter (ceiling is the plan's ``ceiling_db``)."""

    enabled: bool = True
    true_peak: bool = True  # measure on a 4x oversampled signal so inter-sample peaks respect the ceiling
    lookahead_ms: float = 3.0
    release_ms: float = 120.0


@dataclass
class TrackEntry:
    id: str
    path: str
    bpm: float
    offset_s: float
    downbeat: int
    duration_s: float
    confidence: float = 0.0  # downbeat confidence from analysis, informational
    sections: list[dict] = field(default_factory=list)  # detected structure, informational (see analysis.py)

    @property
    def grid(self) -> BeatGrid:
        return BeatGrid(self.offset_s, self.bpm, self.downbeat)

    @property
    def n_beats(self) -> int:
        return self.grid.n_beats(self.duration_s)


@dataclass
class Transition:
    from_track: str
    to_track: str
    out_beat: int  # beat of the outgoing track where the overlap starts
    in_beat: int  # beat of the incoming track that lands on out_beat's slot
    overlap_beats: int = 32
    style: str = "bass_swap"
    eq: EQCurves = field(default_factory=EQCurves)
    score: float | None = None  # planner's score for this choice, informational
    note: str = ""  # why the planner chose it, informational
    alternatives: list[dict] = field(default_factory=list)  # runners-up: {out_beat, in_beat, score, note}


@dataclass
class MixPlan:
    tracks: list[TrackEntry]
    transitions: list[Transition]
    sample_rate: int = 44100
    bit_depth: int = 24
    ceiling_db: float = -1.0
    limiter: LimiterSettings = field(default_factory=LimiterSettings)
    version: int = PLAN_VERSION

    def track(self, track_id: str) -> TrackEntry:
        for t in self.tracks:
            if t.id == track_id:
                return t
        raise KeyError(track_id)

    # ---------- (de)serialisation ----------
    def to_dict(self) -> dict:
        return asdict(self)

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")

    @classmethod
    def from_dict(cls, d: dict) -> MixPlan:
        if d.get("version") != PLAN_VERSION:
            raise PlanError(f"unsupported plan version {d.get('version')!r} (expected {PLAN_VERSION})")
        try:
            tracks = [TrackEntry(**t) for t in d["tracks"]]
            transitions = []
            for t in d["transitions"]:
                t = dict(t)
                eq = EQCurves(**{k: tuple(v) if isinstance(v, list) else v for k, v in t.pop("eq", {}).items()})
                transitions.append(Transition(eq=eq, **t))
            plan = cls(
                tracks=tracks,
                transitions=transitions,
                sample_rate=d.get("sample_rate", 44100),
                bit_depth=d.get("bit_depth", 24),
                ceiling_db=d.get("ceiling_db", -1.0),
                limiter=LimiterSettings(**d.get("limiter", {})),
            )
        except (KeyError, TypeError) as e:
            raise PlanError(f"malformed plan: {e}") from e
        return plan

    @classmethod
    def load(cls, path: str | Path) -> MixPlan:
        try:
            d = json.loads(Path(path).read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise PlanError(f"{path}: invalid JSON: {e}") from e
        plan = cls.from_dict(d)
        base = Path(path).resolve().parent
        for t in plan.tracks:  # relative audio paths are relative to the plan file
            if not Path(t.path).is_absolute():
                t.path = str(base / t.path)
        return plan


def _range_ok(r) -> bool:
    return len(r) == 2 and 0.0 <= r[0] < r[1] <= 1.0


def validate(plan: MixPlan) -> list[str]:
    """Return a list of human-readable problems (empty list = valid)."""
    errs: list[str] = []
    if plan.sample_rate < 8000 or plan.sample_rate > 192000:
        errs.append(f"sample_rate {plan.sample_rate} out of range")
    if plan.bit_depth not in BIT_DEPTHS:
        errs.append(f"bit_depth must be one of {BIT_DEPTHS}, got {plan.bit_depth}")
    if not plan.ceiling_db <= 0:
        errs.append("ceiling_db must be <= 0")
    if not 0.5 <= plan.limiter.lookahead_ms <= 20:
        errs.append("limiter.lookahead_ms must be 0.5..20")
    if not 10 <= plan.limiter.release_ms <= 2000:
        errs.append("limiter.release_ms must be 10..2000")
    if len(plan.tracks) < 1:
        errs.append("plan needs at least one track")
    ids = [t.id for t in plan.tracks]
    if len(set(ids)) != len(ids):
        errs.append("track ids must be unique")
    for t in plan.tracks:
        if not 40 <= t.bpm <= 300:
            errs.append(f"track {t.id}: bpm {t.bpm} outside 40..300")
        if not 0 <= t.downbeat < BEATS_PER_BAR:
            errs.append(f"track {t.id}: downbeat must be 0..{BEATS_PER_BAR - 1}")
        if t.offset_s < 0:
            errs.append(f"track {t.id}: offset_s must be >= 0")
        if t.duration_s <= 0:
            errs.append(f"track {t.id}: duration_s must be > 0")
    if len(plan.transitions) != max(0, len(plan.tracks) - 1):
        errs.append(f"expected {max(0, len(plan.tracks) - 1)} transitions for {len(plan.tracks)} tracks, "
                    f"got {len(plan.transitions)}")
        return errs
    for i, tr in enumerate(plan.transitions):
        where = f"transition {i + 1} ({tr.from_track} -> {tr.to_track})"
        if tr.from_track != plan.tracks[i].id or tr.to_track != plan.tracks[i + 1].id:
            errs.append(f"{where}: must connect tracks '{plan.tracks[i].id}' -> '{plan.tracks[i + 1].id}' in order")
            continue
        a, b = plan.tracks[i], plan.tracks[i + 1]
        if tr.style not in STYLES:
            errs.append(f"{where}: style must be one of {STYLES}")
        if tr.overlap_beats < 2:
            errs.append(f"{where}: overlap_beats must be >= 2")
        # the beat that ends the overlap must itself lie inside the file
        if tr.out_beat < 0 or tr.out_beat + tr.overlap_beats > a.n_beats - 1:
            errs.append(f"{where}: out_beat {tr.out_beat} + overlap {tr.overlap_beats} runs past the end of "
                        f"'{a.id}' (last beat {a.n_beats - 1})")
        if tr.in_beat < 0 or tr.in_beat + tr.overlap_beats > b.n_beats - 1:
            errs.append(f"{where}: in_beat {tr.in_beat} + overlap {tr.overlap_beats} runs past the end of "
                        f"'{b.id}' (last beat {b.n_beats - 1})")
        eq = tr.eq
        for name in ("mid_in", "mid_out", "hi_in", "hi_out"):
            if not _range_ok(getattr(eq, name)):
                errs.append(f"{where}: eq.{name} must be [start, end] with 0 <= start < end <= 1")
        if not 0 <= eq.bass_swap_center <= 1 or eq.bass_swap_beats <= 0:
            errs.append(f"{where}: eq.bass_swap_center must be 0..1 and bass_swap_beats > 0")
        if i > 0:
            prev = plan.transitions[i - 1]
            free_from = prev.in_beat + prev.overlap_beats
            if tr.out_beat < free_from:
                errs.append(f"{where}: out_beat {tr.out_beat} is before '{a.id}' has finished entering "
                            f"(beat {free_from}); the two overlaps would collide")
    return errs


def check(plan: MixPlan) -> MixPlan:
    errs = validate(plan)
    if errs:
        raise PlanError("invalid plan:\n  - " + "\n  - ".join(errs))
    return plan
