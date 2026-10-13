# SPDX-License-Identifier: GPL-3.0-or-later
"""Steady-tempo beat-grid, downbeat and loudness analysis, with an on-disk cache keyed by file content."""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

import librosa
import numpy as np

from .audio import load_audio

ANALYSIS_VERSION = 1
HOP = 64
NFFT = 1024
MIN_DURATION_S = 10.0


class AnalysisError(RuntimeError):
    pass


@dataclass
class TrackAnalysis:
    path: str
    sample_rate: int
    duration_s: float
    bpm: float
    offset_s: float  # time of grid beat 0
    downbeat: int  # beat index (0..3) that starts a bar
    downbeat_confidence: float  # best / second-best phase score; ~1.0 means "could be any beat"
    bar_rms: list[float] = field(default_factory=list)  # loudness per bar from the first downbeat
    version: int = ANALYSIS_VERSION


def default_cache_dir() -> Path:
    env = os.environ.get("BLENDSCAPE_CACHE")
    if env:
        return Path(env)
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return Path(base) / "blendscape"


def _file_key(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return f"{h.hexdigest()}-v{ANALYSIS_VERSION}"


def analyze(path: str | Path, cache_dir: str | Path | None = None, use_cache: bool = True) -> TrackAnalysis:
    p = Path(path)
    if not p.exists():
        raise AnalysisError(f"no such file: {p}")
    cdir = Path(cache_dir) if cache_dir else default_cache_dir()
    cfile = None
    if use_cache:
        try:
            cfile = cdir / f"{_file_key(p)}.json"
            if cfile.exists():
                d = json.loads(cfile.read_text())
                d["path"] = str(p)
                return TrackAnalysis(**d)
        except (OSError, ValueError, TypeError):
            cfile = None
    result = _analyze_uncached(p)
    if use_cache and cfile is not None:
        try:
            cfile.parent.mkdir(parents=True, exist_ok=True)
            cfile.write_text(json.dumps(asdict(result)))
        except OSError:
            pass
    return result


def _band_flux(S: np.ndarray, sr: int, lo: float, hi: float) -> np.ndarray:
    f = librosa.fft_frequencies(sr=sr, n_fft=NFFT)
    m = (f >= lo) & (f < hi)
    L = np.log1p(30 * S[m])
    d = np.maximum(0, np.diff(L, axis=1, prepend=L[:, :1]))
    return d.sum(0)


def _robust_line(k: np.ndarray, t: np.ndarray, w: np.ndarray) -> tuple[float, float]:
    """Weighted least-squares fit of beat time ``t`` against beat number ``k`` (slope = period, intercept = offset),
    repeatedly discarding peaks that sit more than a few ms off the line (e.g. hats/pads in a kick-less intro)."""
    keep = np.ones(len(k), bool)
    slope, icpt = 0.0, 0.0
    for _ in range(6):
        A = np.vstack([k[keep], np.ones(keep.sum())]).T * np.sqrt(w[keep])[:, None]
        sol, *_ = np.linalg.lstsq(A, t[keep] * np.sqrt(w[keep]), rcond=None)
        slope, icpt = sol
        res = t - (slope * k + icpt)
        mad = np.median(np.abs(res[keep]))
        limit = max(0.004, 3.0 * 1.4826 * mad)
        new = np.abs(res) < limit
        if new.sum() < 8 or np.array_equal(new, keep):
            break
        keep = new
    return float(slope), float(icpt)


def _analyze_uncached(p: Path) -> TrackAnalysis:
    x, sr = load_audio(p)
    y = x.mean(1)
    dur = len(y) / sr
    if dur < MIN_DURATION_S:
        raise AnalysisError(f"{p.name} is only {dur:.1f}s long; need at least {MIN_DURATION_S:.0f}s to find a beat")
    S = np.abs(librosa.stft(y, n_fft=NFFT, hop_length=HOP))
    low, mid, high = (_band_flux(S, sr, a, b) for a, b in ((30, 150), (200, 800), (6000, 16000)))
    del S

    def z(v):
        return v / (np.percentile(v, 99) + 1e-9)

    env = 2.0 * z(low) + 0.6 * z(mid) + 0.4 * z(high)
    fps = sr / HOP
    # coarse tempo from librosa on a decimated envelope (keeps its autocorrelation small)
    dec = 8
    env_d = env[: len(env) // dec * dec].reshape(-1, dec).max(1)
    t0 = float(np.atleast_1d(librosa.feature.tempo(onset_envelope=env_d, sr=sr, hop_length=HOP * dec,
                                                   start_bpm=125, max_tempo=180))[0])
    while t0 < 100:
        t0 *= 2
    while t0 > 180:
        t0 /= 2

    def score(bpm: float, off_s: float) -> float:
        period = 60 / bpm
        times = np.arange(off_s, len(env) / fps - 0.5, period)
        idx = times * fps
        i0 = np.floor(idx).astype(int)
        fr = idx - i0
        ok = i0 + 1 < len(env)
        i0, fr = i0[ok], fr[ok]
        return float(np.mean(env[i0] * (1 - fr) + env[i0 + 1] * fr))

    best = (-1.0, t0, 0.0)
    for bpm in np.arange(t0 * 0.97, t0 * 1.03, 0.05):
        period = 60 / bpm
        for off in np.arange(0, period, 2 / fps):
            s = score(bpm, off)
            if s > best[0]:
                best = (s, bpm, off)
    _, bpm, off = best
    # refine: per-beat local envelope peaks, least squares for period and offset
    period = 60 / bpm
    for _ in range(2):
        beats = np.arange(off, len(y) / sr - 0.5, period)
        pk = []
        for b in beats:
            a, c = int((b - 0.03) * fps), int((b + 0.03) * fps) + 1
            if a < 0 or c >= len(env):
                continue
            seg = env[a:c]
            if seg.max() < 0.2:
                continue
            pk.append((round((b - off) / period), (a + int(np.argmax(seg))) / fps, float(seg.max())))
        if len(pk) < 8:
            raise AnalysisError(f"{p.name}: could not find a steady beat")
        k, t, h = np.array(pk).T
        period, off = _robust_line(k, t, h)
    # sample-level offset refinement: STFT flux peaks lead the true onset by about half a window
    w = max(1, int(0.002 * sr))
    e = np.convolve(y ** 2, np.ones(w) / w, "same")
    d = np.maximum(0, np.diff(e, prepend=e[0]))
    bt_all = np.arange(off, len(y) / sr - 0.5, period)
    lags = np.arange(-int(0.02 * sr), int(0.02 * sr) + 1)
    sc = np.zeros(len(lags))
    for b in bt_all:
        c = int(b * sr)
        if c + lags[0] < 0 or c + lags[-1] >= len(d):
            continue
        sc += d[c + lags[0]: c + lags[-1] + 1]
    off += lags[int(np.argmax(sc))] / sr
    bpm = 60 / period
    off = off % period
    # downbeat: mid-band flux at beats folded into four phases
    nb = int((len(y) / sr - off) / period)
    bt = off + period * np.arange(nb)
    zm = z(mid)
    win = int(0.02 * fps)
    mf = np.array([zm[max(0, int(t_ * fps) - win): int(t_ * fps) + win].max()
                   if int(t_ * fps) + win < len(zm) else 0.0 for t_ in bt])
    phases = [float(mf[q::4].mean()) for q in range(4)]
    db = int(np.argmax(phases))
    ranked = sorted(phases)
    conf = ranked[-1] / (ranked[-2] + 1e-9)
    bars = []
    for bi in range((nb - db) // 4):
        s = int((off + (db + 4 * bi) * period) * sr)
        e_ = int((off + (db + 4 * bi + 4) * period) * sr)
        bars.append(float(np.sqrt(np.mean(y[s:e_] ** 2))))
    return TrackAnalysis(path=str(p), sample_rate=sr, duration_s=dur, bpm=float(bpm), offset_s=float(off),
                         downbeat=db, downbeat_confidence=float(conf), bar_rms=bars)
