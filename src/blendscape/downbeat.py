# SPDX-License-Identifier: GPL-3.0-or-later
"""Which of the four beats starts a bar?

Given a steady beat grid (tempo and phase already found), vote on the four candidate bar-line positions using several
independent cues, each a standardised score per candidate:

``kick_accent``   low-band (kick / bass) energy is stronger on beat 1
``mid_accent``    mid-band onsets (chord stabs, pads) are stronger on beat 1
``backbeat``      claps / snares (1-5 kHz) land on beats 2 and 4, so beats 1 and 3 are the quiet ones
``chord_change``  harmony changes at bar lines (chroma before vs after each beat)
``bass_change``   the bass root changes at bar lines (bass-band chroma)
``impact``        crashes / impacts (the loudest high-frequency hits) land on bar lines
``boundary``      big changes in the arrangement (drop, breakdown) land on bar lines

The scores are summed with fixed weights and turned into a posterior with a softmax. Only the *relative* evidence for
the four phases matters, so the result is also a usable confidence: ~0.25 means "no idea", 1.0 means "certain".
Assumes 4/4. The weights and temperature were tuned on synthetic tracks (``tests/synth_rich.py``); real music will
behave differently, which is why the CLI can draw a click track to check by ear and lets you override the result."""
from __future__ import annotations

from dataclasses import dataclass, field

import librosa
import numpy as np
from scipy.ndimage import uniform_filter1d

BEATS_PER_BAR = 4
NFFT, HOP = 1024, 256  # onset / energy analysis
CNFFT, CHOP = 4096, 1024  # chroma analysis
MIN_BEATS = 24

WEIGHTS = {"kick_accent": 1.0, "mid_accent": 1.0, "backbeat": 1.0, "chord_change": 1.0, "bass_change": 1.0,
           "impact": 1.0, "boundary": 1.0}
NOISE_T = 1.5  # |score| below this is what pure noise produces; such cues abstain
TEMPERATURE = 0.35  # scales the summed evidence before the softmax
CLIP = 6.0  # no single cue may contribute more than this per candidate


@dataclass
class DownbeatEstimate:
    phase: int  # grid beat index (0..3) that starts a bar
    probs: list[float]  # posterior for each candidate phase
    confidence: float  # probs[phase]
    cues: dict[str, list[float]] = field(default_factory=dict)  # standardised score per cue and phase


def _band_rows(freqs: np.ndarray, lo: float, hi: float) -> np.ndarray:
    return (freqs >= lo) & (freqs < hi)


def _flux(mag: np.ndarray) -> np.ndarray:
    log = np.log1p(30 * mag)
    return np.maximum(0, np.diff(log, axis=1, prepend=log[:, :1])).sum(0)


def _beat_windows(times_s: np.ndarray, n_frames: int, fps: float, before: float, after: float):
    lo = np.clip(((times_s - before) * fps).astype(int), 0, n_frames - 1)
    hi = np.clip(((times_s + after) * fps).astype(int) + 1, 1, n_frames)
    return lo, np.maximum(hi, lo + 1)


def _per_beat(series: np.ndarray, times_s: np.ndarray, fps: float, before: float, after: float, how: str):
    lo, hi = _beat_windows(times_s, len(series), fps, before, after)
    fn = np.max if how == "max" else np.mean
    return np.array([fn(series[a:b]) for a, b in zip(lo, hi, strict=True)])


def _local_norm(a: np.ndarray, width: int = 17) -> np.ndarray:
    """Divide by the neighbouring average so loud sections do not drown quiet ones."""
    base = uniform_filter1d(a.astype(float), width, mode="nearest")
    return np.clip(a / (base + 1e-9 * (1 + base.max())), 0.0, 6.0)


def _welch(a: np.ndarray, mask: np.ndarray) -> float:
    x, y = a[mask], a[~mask]
    if len(x) < 3 or len(y) < 3:
        return 0.0
    se = np.sqrt(x.var(ddof=1) / len(x) + y.var(ddof=1) / len(y)) + 1e-12
    return float((x.mean() - y.mean()) / se)


def _accent(a: np.ndarray) -> np.ndarray:
    """Per candidate phase: is this beat stronger than the other three?"""
    k = np.arange(len(a)) % BEATS_PER_BAR
    return np.array([_welch(a, k == p) for p in range(BEATS_PER_BAR)])


def _backbeat(a: np.ndarray) -> np.ndarray:
    """Candidate p says beats p+1 and p+3 carry the claps: compare them with beats p and p+2."""
    k = np.arange(len(a)) % BEATS_PER_BAR
    return np.array([_welch(a, ((k - p) % BEATS_PER_BAR) % 2 == 1) for p in range(BEATS_PER_BAR)])


def _chroma_by_beat(S: np.ndarray, freqs: np.ndarray, lo: float, hi: float, sr: int, frame_t: np.ndarray,
                    beat_t: np.ndarray, period: float) -> np.ndarray:
    keep = (freqs >= lo) & (freqs < hi)
    masked = np.where(keep[:, None], S, 0.0)
    chroma = librosa.feature.chroma_stft(S=masked**2, sr=sr, n_fft=CNFFT, norm=None)
    out = np.zeros((len(beat_t), 12))
    for i, t in enumerate(beat_t):
        sel = (frame_t >= t) & (frame_t < t + period)
        if sel.any():
            out[i] = chroma[:, sel].mean(axis=1)
    return out


def _change_at_beats(c: np.ndarray, span: int = 3) -> np.ndarray:
    """Distance between the average chroma of the ``span`` beats before and after each beat."""
    n = len(c)
    norm = c / (np.linalg.norm(c, axis=1, keepdims=True) + 1e-12)
    out = np.zeros(n)
    for k in range(span, n - span + 1):
        before, after = norm[k - span:k].mean(axis=0), norm[k:k + span].mean(axis=0)
        denom = np.linalg.norm(before) * np.linalg.norm(after) + 1e-12
        out[k] = 1.0 - float(before @ after / denom)
    return out


def _impact(a: np.ndarray, quantile: float = 0.95) -> np.ndarray:
    """The very strongest hits (crash cymbals, impacts) vote for the phase of the beat they fall on."""
    hits = np.flatnonzero(a >= np.quantile(a, quantile))
    if len(hits) < 4:
        return np.zeros(BEATS_PER_BAR)
    counts = np.bincount(hits % BEATS_PER_BAR, minlength=BEATS_PER_BAR) / len(hits)
    return (counts - 0.25) / np.sqrt(0.25 * 0.75 / len(hits))


def _boundary(feat: np.ndarray, window: int = 8) -> np.ndarray:
    """Arrangement changes (a drop, a breakdown) vote for the phase of the beat they land on."""
    n = feat.shape[0]
    csum = np.vstack([np.zeros((1, feat.shape[1])), np.cumsum(feat, axis=0)])
    nov = np.zeros(n)
    for k in range(window, n - window + 1):
        after = (csum[k + window] - csum[k]) / window
        before = (csum[k] - csum[k - window]) / window
        nov[k] = float(np.abs(after - before).mean())
    if nov.max() <= 0:
        return np.zeros(BEATS_PER_BAR)
    thr = max(0.2, 0.25 * nov.max())
    peaks = [k for k in range(1, n - 1) if nov[k] >= thr and nov[k] >= nov[k - 1] and nov[k] > nov[k + 1]]
    # neighbouring beats of one change all look novel; keep only the strongest in any 4-beat stretch
    chosen: list[int] = []
    for k in sorted(peaks, key=lambda p: -nov[p]):
        if all(abs(k - c) >= BEATS_PER_BAR for c in chosen):
            chosen.append(k)
    if len(chosen) < 2:
        return np.zeros(BEATS_PER_BAR)
    w = np.array([nov[k] for k in chosen])
    ph = np.array([k % BEATS_PER_BAR for k in chosen])
    share = np.array([w[ph == p].sum() for p in range(BEATS_PER_BAR)]) / w.sum()
    n_eff = w.sum() ** 2 / (w**2).sum()
    se = np.sqrt(0.25 * 0.75 / max(n_eff, 1.0))
    return (share - 0.25) / se


def _vote(score: np.ndarray) -> np.ndarray:
    """Noise-level scores abstain; the rest are shrunk by the noise level and clipped."""
    return np.clip(np.sign(score) * np.maximum(0.0, np.abs(score) - NOISE_T), -CLIP, CLIP)


def _softmax(x: np.ndarray) -> np.ndarray:
    e = np.exp(x - x.max())
    return e / e.sum()


def estimate_downbeat(y: np.ndarray, sr: int, offset_s: float, period_s: float) -> DownbeatEstimate:
    """Pick the bar-line phase for the grid ``beat k at offset_s + k * period_s`` in mono signal ``y``."""
    dur = len(y) / sr
    nb = int((dur - offset_s) / period_s)
    uniform = DownbeatEstimate(0, [0.25] * 4, 0.25)
    if nb < MIN_BEATS:
        return uniform
    beat_t = offset_s + period_s * np.arange(nb)

    # onset / energy features
    S = np.abs(librosa.stft(y, n_fft=NFFT, hop_length=HOP))
    f = librosa.fft_frequencies(sr=sr, n_fft=NFFT)
    fps = sr / HOP
    low_e = S[_band_rows(f, 30, 150)].sum(0)
    mid_e = S[_band_rows(f, 200, 2000)].sum(0)
    high_e = S[_band_rows(f, 6000, 16000)].sum(0)
    mid_flux = _flux(S[_band_rows(f, 200, 2000)])
    clap_flux = _flux(S[_band_rows(f, 1000, 5000)])
    del S

    after = 0.5 * period_s
    kick = _local_norm(_per_beat(low_e, beat_t, fps, 0.01, after, "mean"))
    midf = _local_norm(_per_beat(mid_flux, beat_t, fps, 0.03, 0.05, "max"))
    clap = _local_norm(_per_beat(clap_flux, beat_t, fps, 0.03, 0.05, "max"))

    # harmony
    Sc = np.abs(librosa.stft(y, n_fft=CNFFT, hop_length=CHOP))
    cf = librosa.fft_frequencies(sr=sr, n_fft=CNFFT)
    frame_t = librosa.frames_to_time(np.arange(Sc.shape[1]), sr=sr, hop_length=CHOP, n_fft=CNFFT)
    chord = _change_at_beats(_chroma_by_beat(Sc, cf, 250, 2000, sr, frame_t, beat_t, period_s))
    bass = _change_at_beats(_chroma_by_beat(Sc, cf, 40, 250, sr, frame_t, beat_t, period_s))
    del Sc

    # arrangement: per-beat band levels in dB (12 dB = 1.0), so a 6 dB change in any band counts as 0.5
    levels = []
    for e in (low_e, mid_e, high_e):
        # centred on the beat, not starting at it: a kick a few ms early must not leak into the previous beat
        db = 20 * np.log10(np.maximum(_per_beat(e, beat_t, fps, 0.1 * period_s, 0.9 * period_s, "mean"), 1e-6))
        levels.append(np.maximum(db, np.percentile(db, 95) - 60) / 12.0)  # floor: silence is not "infinitely quiet"
    feat = np.stack(levels, axis=1)

    cues = {
        "kick_accent": _accent(kick),
        "mid_accent": _accent(_local_norm(midf)),
        "backbeat": _backbeat(clap),
        "chord_change": _accent(_local_norm(chord)),
        "bass_change": _accent(_local_norm(bass)),
        "impact": _impact(clap),
        "boundary": _boundary(feat),
    }
    total = sum(WEIGHTS[name] * _vote(score) for name, score in cues.items())
    probs = _softmax(TEMPERATURE * total)
    phase = int(np.argmax(probs))
    return DownbeatEstimate(phase, [float(p) for p in probs], float(probs[phase]),
                            {k: [round(float(x), 2) for x in v] for k, v in cues.items()})
