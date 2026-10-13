# Milestone 0 spike

Goal: prove the blend (beat-match, phase-align, 3-band bass swap) on at least a few track pairs and decide
the analyzer and stretcher.

## Run

```
python -m venv .venv && . .venv/bin/activate && pip install -r spike/requirements.txt
sudo apt install librubberband2          # Rubber Band C library (GPL)
cd spike
python synth.py                          # generates fixtures/*.wav (synthetic 124/128/126 BPM dance tracks)
python evaluate.py                       # objective checks
python blend.py out/mix.wav a.wav b.wav [c.wav ...] --beats 32 [--out-bar N --in-bar M]
```

`blend.py` accepts any ordered list of wavs, so try it on real tracks.

## Files

- `synth.py`: synthetic tracks with known BPM / downbeat offset (intro, body, breakdown, body, outro).
- `analyze.py`: steady-tempo grid (BPM + offset), downbeat phase, per-bar RMS.
- `warp.py`: beat-map time warp, Rubber Band R3 engine, offline mode + key-frame map via ctypes.
- `blend.py`: mix-point selection on the phrase grid, tempo glide, LR4 3-band split, EQ curves, chaining.
- `evaluate.py`: alignment, continuity, bass-doubling and clipping checks.

## Findings (synthetic material only)

| Check | Result |
|---|---|
| BPM estimate | within 0.001 BPM of truth on 3 tracks |
| First-downbeat position | within 1 ms of truth (needed a sample-level refinement; STFT flux alone was 10 ms early) |
| A vs B beat alignment in the overlap | mean < 1 ms; 16-beat overlaps max 2.9 ms; 32-beat overlap max 6.6 ms (std 2.8 ms) |
| Continuity at overlap boundaries | no discontinuity (step 0.017 vs 0.6 typical) |
| Bass doubling during the swap | low-band level never above 1.02x of the louder single track |
| Chaining A->B->C | works; the tail of each blend keeps an exact grid (analytic) |
| Render speed | about 40 s end to end (analysis + warp + render) for a 2-track, ~200 s mix; not profiled |

## Decisions

- **Stretcher: Rubber Band R3 (finer engine) through its C API.**
  - `pylibrb` does not expose `set_keyframe_map` (its `std::map` binding is missing).
  - The real-time engine has a ratio-dependent latency (~1.3k to 16k samples), so it cannot place beats sample-accurately.
  - Offline mode with a key-frame map is exact, so we call `librubberband` through ctypes. Drop the `pylibrb` dependency in the real code.
- **Analyzer: own grid fit** (envelope comb search + least squares + sample-level refinement) works for steady tempo and beats librosa's `beat_track` for this purpose. Keep it pluggable; try `beat_this` for downbeats on real material.
- **Tempo glide:** both tracks are mapped beat-for-beat to a master grid whose period glides linearly from A's to B's over the overlap. This keeps beats locked throughout.

## Open issues / not yet proven

- **No real music was tested.** The exit criterion ("sounds like a DJ mix on 3+ real pairs") needs real
  tracks and ears. Run `blend.py` on yours and listen.
- Downbeat confidence is low even on synthetic data (best/second-best ratio 1.04-1.19). Needs a better
  downbeat model and a manual override in the GUI.
- Rubber Band prints `NaN ... time ratio` warnings on init (harmless, output ratio verified); investigate.
- Mixes can exceed 0 dBFS when summing two loud tracks (peak 1.08 on a 3-track chain): a real limiter and loudness stage is needed (the spike only scales).
- 32-beat glides show up to 6.6 ms jitter: check with real stretch ratios and consider re-anchoring the warp every bar.
- Phrase origin is the first detected downbeat; real tracks with silent intros or pickup bars will need a better origin.
