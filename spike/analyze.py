# SPDX-License-Identifier: GPL-3.0-or-later
"""Steady-tempo beat grid + downbeat + section analysis for the spike."""
import numpy as np, soundfile as sf, librosa

HOP = 64
NFFT = 1024


def _band_flux(S, sr, lo, hi):
    f = librosa.fft_frequencies(sr=sr, n_fft=NFFT)
    m = (f >= lo) & (f < hi)
    L = np.log1p(30 * S[m])
    d = np.maximum(0, np.diff(L, axis=1, prepend=L[:, :1]))
    return d.sum(0)


def load(path):
    x, sr = sf.read(path, dtype="float32", always_2d=True)
    return x, sr


def analyze(path):
    x, sr = load(path)
    y = x.mean(1)
    S = np.abs(librosa.stft(y, n_fft=NFFT, hop_length=HOP))
    low, mid, high = (_band_flux(S, sr, a, b) for a, b in ((30, 150), (200, 800), (6000, 16000)))
    z = lambda v: v / (np.percentile(v, 99) + 1e-9)
    env = 2.0 * z(low) + 0.6 * z(mid) + 0.4 * z(high)
    fps = sr / HOP
    # coarse tempo from librosa on the same envelope
    dec = 8  # decimate the envelope for tempo estimation (hop 512) to keep librosa's autocorrelation small
    env_d = env[: len(env) // dec * dec].reshape(-1, dec).max(1)
    t0 = float(np.atleast_1d(librosa.feature.tempo(onset_envelope=env_d, sr=sr, hop_length=HOP * dec,
                                                   start_bpm=125, max_tempo=180))[0])
    while t0 < 100:
        t0 *= 2
    while t0 > 180:
        t0 /= 2

    def score(bpm, off_s):
        P = 60 / bpm
        times = np.arange(off_s, len(env) / fps - 0.5, P)
        idx = times * fps
        i0 = np.floor(idx).astype(int)
        fr = idx - i0
        ok = i0 + 1 < len(env)
        i0, fr = i0[ok], fr[ok]
        return float(np.mean(env[i0] * (1 - fr) + env[i0 + 1] * fr))

    best = (-1, None, None)
    for bpm in np.arange(t0 * 0.97, t0 * 1.03, 0.05):
        P = 60 / bpm
        for off in np.arange(0, P, 1 / fps * 2):
            s = score(bpm, off)
            if s > best[0]:
                best = (s, bpm, off)
    _, bpm, off = best
    # fine refinement: local peak per beat, then least squares
    P = 60 / bpm
    for _ in range(2):
        beats = np.arange(off, len(y) / sr - 0.5, P)
        pk = []
        for b in beats:
            a, c = int((b - 0.03) * fps), int((b + 0.03) * fps) + 1
            if a < 0 or c >= len(env):
                continue
            seg = env[a:c]
            if seg.max() < 0.2:
                continue
            j = int(np.argmax(seg))
            pk.append((round((b - off) / P), (a + j) / fps))
        k, t = np.array(pk).T
        A = np.vstack([k, np.ones_like(k)]).T
        (P, off), *_ = np.linalg.lstsq(A, t, rcond=None)
    # sample-level refinement of the offset: STFT flux peaks lead the true onset by ~half a window
    e = np.convolve(y ** 2, np.ones(int(0.002 * sr)) / int(0.002 * sr), "same")
    d = np.maximum(0, np.diff(e, prepend=e[0]))
    bt_all = np.arange(off, len(y) / sr - 0.5, P)
    lags = np.arange(-int(0.02 * sr), int(0.02 * sr) + 1)
    sc = np.zeros(len(lags))
    for b in bt_all[: len(bt_all) // 2 * 2]:
        c = int(b * sr)
        if c + lags[0] < 0 or c + lags[-1] >= len(d):
            continue
        sc += d[c + lags[0]: c + lags[-1] + 1]
    off += lags[int(np.argmax(sc))] / sr
    bpm = 60 / P
    off = off % P
    # downbeat: mid-band flux at beats, folded mod 4
    nb = int((len(y) / sr - off) / P)
    bt = off + P * np.arange(nb)
    zm = z(mid)
    w = int(0.02 * fps)
    mf = np.array([zm[max(0, int(t * fps) - w):int(t * fps) + w].max() if int(t * fps) + w < len(env) else 0 for t in bt])
    ph = [mf[p::4].mean() for p in range(4)]
    db = int(np.argmax(ph))
    # per-bar loudness profile (low-band RMS and total RMS)
    bars = []
    for bi in range((nb - db) // 4):
        s = int((off + (db + 4 * bi) * P) * sr)
        e = int((off + (db + 4 * bi + 4) * P) * sr)
        seg = y[s:e]
        bars.append(float(np.sqrt(np.mean(seg ** 2))))
    return dict(path=str(path), sr=sr, n=len(y), bpm=float(bpm), period_s=float(P), offset_s=float(off), downbeat=db,
                downbeat_conf=float(sorted(ph)[-1] / (sorted(ph)[-2] + 1e-9)), bar_rms=bars, n_beats=nb)


if __name__ == "__main__":
    import json, sys, pathlib
    truth = json.loads(pathlib.Path("fixtures/truth.json").read_text())
    for t in truth:
        a = analyze(t["path"])
        # ground-truth first downbeat: the synth starts bar 1 at `offset`
        P = 60 / t["bpm"]
        dbt = a["offset_s"] + a["downbeat"] * a["period_s"]
        err_db = ((dbt - t["offset"] + P * 2) % (4 * P)) - P * 2
        print(f'{pathlib.Path(t["path"]).name}: bpm {a["bpm"]:.3f} (truth {t["bpm"]}), '
              f'1st downbeat err {err_db*1000:+.1f} ms, db conf {a["downbeat_conf"]:.2f}')
