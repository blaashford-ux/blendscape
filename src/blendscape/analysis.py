# SPDX-License-Identifier: GPL-3.0-or-later
"""Steady-tempo beat-grid, downbeat and loudness analysis, with an on-disk cache keyed by file content."""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

import librosa
import numpy as np
from scipy.signal import butter, sosfilt

from .audio import load_audio
from .downbeat import estimate_downbeat
from .sections import detect_sections

ANALYSIS_VERSION = 4
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
    downbeat_confidence: float  # posterior of the chosen beat: 0.25 = no idea, 1.0 = certain (1.0 if set by hand)
    bar_rms: list[float] = field(default_factory=list)  # loudness per bar from the first downbeat
    bar_low: list[float] = field(default_factory=list)  # per-bar RMS of the low band (<150 Hz: kick and bass)
    bar_mid: list[float] = field(default_factory=list)  # ... 200 Hz - 2 kHz
    bar_high: list[float] = field(default_factory=list)  # ... above 5 kHz
    sections: list[dict] = field(default_factory=list)  # {"label", "start_bar", "end_bar"}; bar 0 = first downbeat
    downbeat_probs: list[float] = field(default_factory=list)  # posterior for each of the four candidate beats
    downbeat_manual: bool = False  # True if the downbeat was set by hand rather than detected
    active_end_s: float = 0.0  # where audible content ends (trailing silence / very quiet fade trimmed); 0 = unknown
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


def active_end(y: np.ndarray, sr: int, floor_db: float = 45.0) -> float:
    """Time of the last frame within ``floor_db`` of the track's typical loud level (so a silent or near-silent tail
    does not count as music)."""
    hop = 1024
    n = len(y) // hop
    if n < 2:
        return len(y) / sr
    rms = np.sqrt((y[: n * hop].reshape(n, hop) ** 2).mean(axis=1))
    db = 20 * np.log10(rms + 1e-9)
    live = np.flatnonzero(db > np.percentile(db, 95) - floor_db)
    return float(min(len(y) / sr, (live[-1] + 1) * hop / sr)) if len(live) else len(y) / sr


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
    bpm = 60 / period
    off = off % period
    est = estimate_downbeat(y, sr, off, period)
    bar_low, bar_mid, bar_high, bar_full, secs = _bar_profile(y, sr, off, period, est.phase)
    return TrackAnalysis(path=str(p), sample_rate=sr, duration_s=dur, bpm=float(bpm), offset_s=float(off),
                         downbeat=est.phase, downbeat_confidence=est.confidence, bar_rms=bar_full, bar_low=bar_low,
                         bar_mid=bar_mid, bar_high=bar_high, sections=secs, downbeat_probs=est.probs,
                         active_end_s=round(active_end(y, sr), 3))


def _bar_profile(y: np.ndarray, sr: int, off: float, period: float, db: int):
    """Per-bar loudness (full / low / mid / high) and detected sections, bars counted from the first downbeat."""
    nb = int((len(y) / sr - off) / period)
    n_bars = (nb - db) // 4
    edges = [int((off + (db + 4 * bi) * period) * sr) for bi in range(n_bars + 1)]
    bands = {
        "full": y,
        "low": sosfilt(butter(4, 150, "low", fs=sr, output="sos"), y),
        "mid": sosfilt(butter(4, [200, 2000], "band", fs=sr, output="sos"), y),
        "high": sosfilt(butter(4, 5000, "high", fs=sr, output="sos"), y),
    }
    bar = {k: [round(float(np.sqrt(np.mean(v[a:b] ** 2))), 6) for a, b in zip(edges[:-1], edges[1:], strict=True)]
           for k, v in bands.items()}
    secs = [s.to_dict() for s in detect_sections(bar["low"], bar["mid"], bar["high"])]
    return bar["low"], bar["mid"], bar["high"], bar["full"], secs


def with_downbeat(a: TrackAnalysis, downbeat: int) -> TrackAnalysis:
    """Return ``a`` with the bar line forced onto grid beat ``downbeat`` (0..3): the per-bar profile and sections
    are recomputed to match. Use when detection picked the wrong beat."""
    if not 0 <= downbeat < 4:
        raise AnalysisError("downbeat must be 0, 1, 2 or 3 (which grid beat starts a bar)")
    if downbeat == a.downbeat and not a.downbeat_manual:
        return replace(a, downbeat_manual=True, downbeat_confidence=1.0)
    x, _ = load_audio(a.path)
    low, mid, high, full, secs = _bar_profile(x.mean(1), a.sample_rate, a.offset_s, 60 / a.bpm, downbeat)
    probs = [1.0 if i == downbeat else 0.0 for i in range(4)]
    return replace(a, downbeat=downbeat, downbeat_confidence=1.0, downbeat_manual=True, downbeat_probs=probs,
                   bar_rms=full, bar_low=low, bar_mid=mid, bar_high=high, sections=secs)
