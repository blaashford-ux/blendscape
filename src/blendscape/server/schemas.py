# SPDX-License-Identifier: GPL-3.0-or-later
"""Request and settings models for the API."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class LimiterIn(BaseModel):
    enabled: bool = True
    true_peak: bool = True
    lookahead_ms: float = Field(3.0, ge=0.5, le=20)
    release_ms: float = Field(120.0, ge=10, le=2000)


class Settings(BaseModel):
    """Project-wide planning and rendering settings."""

    overlap_beats: int = Field(32, ge=2, le=256)
    overlap_seconds: float | None = Field(None, gt=0, le=60)  # overrides overlap_beats when set
    style: Literal["bass_swap", "crossfade"] = "bass_swap"
    phrase_bars: int = Field(8, ge=1, le=16)
    mix_points: Literal["smart", "latest"] = "smart"
    max_cut_seconds: float = Field(10.0, ge=0, le=120)  # 0 = no limit
    sample_rate: Literal[44100, 48000, 88200, 96000] = 44100
    bit_depth: Literal[16, 24, 32] = 24
    ceiling_db: float = Field(-1.0, ge=-24, le=0)
    limiter: LimiterIn = LimiterIn()


class SettingsPatch(BaseModel):
    """Any subset of :class:`Settings`; unknown fields are rejected."""

    model_config = {"extra": "forbid"}
    overlap_beats: int | None = Field(None, ge=2, le=256)
    overlap_seconds: float | None = Field(None, gt=0, le=60)
    clear_overlap_seconds: bool = False
    style: Literal["bass_swap", "crossfade"] | None = None
    phrase_bars: int | None = Field(None, ge=1, le=16)
    mix_points: Literal["smart", "latest"] | None = None
    max_cut_seconds: float | None = Field(None, ge=0, le=120)
    sample_rate: Literal[44100, 48000, 88200, 96000] | None = None
    bit_depth: Literal[16, 24, 32] | None = None
    ceiling_db: float | None = Field(None, ge=-24, le=0)
    limiter: LimiterIn | None = None


class ProjectCreate(BaseModel):
    name: str = Field("Untitled mix", min_length=1, max_length=120)
    settings: Settings | None = None


class ProjectRename(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class AddTrackPath(BaseModel):
    path: str = Field(min_length=1)


class OrderIn(BaseModel):
    order: list[str]


class DownbeatIn(BaseModel):
    downbeat: int | None = Field(None, ge=0, le=3)  # null clears the override


class AnalyzeIn(BaseModel):
    replan: bool = True  # plan again when the analysis is finished
    force: bool = False  # ignore cached analyses


class EqPatch(BaseModel):
    model_config = {"extra": "forbid"}
    bass_swap_center: float | None = Field(None, ge=0, le=1)
    bass_swap_beats: float | None = Field(None, gt=0, le=64)
    mid_in: tuple[float, float] | None = None
    mid_out: tuple[float, float] | None = None
    hi_in: tuple[float, float] | None = None
    hi_out: tuple[float, float] | None = None


class TransitionPatch(BaseModel):
    model_config = {"extra": "forbid"}
    out_beat: int | None = Field(None, ge=0)
    in_beat: int | None = Field(None, ge=0)
    overlap_beats: int | None = Field(None, ge=2, le=256)
    style: Literal["bass_swap", "crossfade"] | None = None
    eq: EqPatch | None = None


class AlternativeIn(BaseModel):
    index: int = Field(ge=0)


class PreviewIn(BaseModel):
    lead_s: float = Field(8.0, ge=0, le=60)
    tail_s: float = Field(8.0, ge=0, le=60)


class RenderIn(BaseModel):
    name: str | None = Field(None, max_length=80)

    @field_validator("name")
    @classmethod
    def _safe(cls, v):
        if v is None:
            return v
        cleaned = "".join(c for c in v if c.isalnum() or c in " -_.").strip().strip(".")
        if not cleaned:
            raise ValueError("name has no usable characters")
        return cleaned
