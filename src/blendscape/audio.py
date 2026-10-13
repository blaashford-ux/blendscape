# SPDX-License-Identifier: GPL-3.0-or-later
"""Reading and writing audio. Internally everything is float32 stereo ``(n, 2)`` at the project sample rate."""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

_SUBTYPES = {16: "PCM_16", 24: "PCM_24", 32: "FLOAT"}


class AudioError(RuntimeError):
    pass


def load_audio(path: str | Path, sample_rate: int | None = None) -> tuple[np.ndarray, int]:
    """Load as float32 stereo, resampling to ``sample_rate`` if given. Returns ``(audio, sr)``."""
    try:
        x, sr = sf.read(str(path), dtype="float32", always_2d=True)
    except (sf.LibsndfileError, RuntimeError, OSError) as e:
        raise AudioError(f"cannot read {path}: {e}") from e
    if x.shape[0] == 0:
        raise AudioError(f"{path} contains no audio")
    if x.shape[1] == 1:
        x = np.repeat(x, 2, axis=1)
    elif x.shape[1] > 2:
        x = x[:, :2]
    if sample_rate and sr != sample_rate:
        x = resample(x, sr, sample_rate)
        sr = sample_rate
    return np.ascontiguousarray(x), sr


def resample(x: np.ndarray, sr_from: int, sr_to: int) -> np.ndarray:
    g = math.gcd(sr_to, sr_from)
    return resample_poly(x, sr_to // g, sr_from // g, axis=0).astype(np.float32)


def write_audio(path: str | Path, audio: np.ndarray, sr: int, bit_depth: int = 24) -> None:
    if bit_depth not in _SUBTYPES:
        raise AudioError(f"bit depth must be one of {sorted(_SUBTYPES)}")
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(p), audio.astype(np.float32), sr, subtype=_SUBTYPES[bit_depth])
