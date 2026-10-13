# Milestone 0 spike

> Superseded by the `blendscape` package in `src/` (milestone 1). Kept for reference; use `blendscape mix ...` instead.

Goal: prove the blend (beat-match, phase-align, 3-band bass swap) on at least a few track pairs and decide
the analyzer and stretcher.

## Run

You need Python 3.10+ and the **Rubber Band command-line tool** (`rubberband`, v3.x or later).

**Windows (PowerShell)**

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1          # if blocked: Set-ExecutionPolicy -Scope Process Bypass
pip install -r spike/requirements.txt
# Download the Windows "executable" zip from https://breakfastquay.com/rubberband/ , unzip it, then either
# add that folder to PATH or point at it for this session:
$env:RUBBERBAND = "C:\path\to\rubberband.exe"
cd spike
python synth.py                       # generates fixtures\*.wav (synthetic test tracks)
python evaluate.py                    # objective checks
python blend.py out\mix.wav a.wav b.wav c.wav --beats 32
```

**Linux / macOS**

```bash
python -m venv .venv && . .venv/bin/activate && pip install -r spike/requirements.txt
sudo apt install rubberband-cli       # or: brew install rubberband
cd spike && python synth.py && python evaluate.py
python blend.py out/mix.wav a.wav b.wav --beats 32 [--out-bar N --in-bar M]
```

`blend.py` accepts any ordered list of wavs (any sample rate/bit depth soundfile can read; all tracks should share
a sample rate for now). Options: `--beats` overlap length in beats; `--out-bar` / `--in-bar` choose the bar where the
overlap starts in the outgoing track / where the incoming track enters (default: the latest 8-bar boundary that fits,
and bar 0 of the incoming track).

## Files

- `synth.py`: synthetic tracks with known BPM / downbeat offset (intro, body, breakdown, body, outro).
- `analyze.py`: steady-tempo grid (BPM + offset), downbeat phase, per-bar RMS.
- `warp.py`: beat-map time warp, Rubber Band R3 engine, offline mode + time map (CLI backend, or ctypes to librubberband).
- `blend.py`: mix-point selection on the phrase grid, tempo glide, LR4 3-band split, EQ curves, chaining.
- `evaluate.py`: alignment, continuity, bass-doubling and clipping checks.

## Findings (synthetic material only)

| Check | Result |
|---|---|
| BPM estimate | within 0.001 BPM of truth on 3 tracks |
| First-downbeat position | within 1 ms of truth (needed a sample-level refinement; STFT flux alone was 10 ms early) |
| A vs B beat alignment in the overlap | mean < 1 ms, worst single beat 1.3 ms (16 and 32 beat overlaps, CLI backend) |
| Continuity at overlap boundaries | no discontinuity (step 0.017 vs 0.6 typical) |
| Bass doubling during the swap | low-band level never above 1.02x of the louder single track |
| Chaining A->B->C | works; the tail of each blend keeps an exact grid (analytic) |
| Render speed | about 40 s end to end (analysis + warp + render) for a 2-track, ~200 s mix; not profiled |

## Decisions

- **Stretcher: Rubber Band R3 (finer engine), offline mode with a time map, via the `rubberband` CLI.**
  - `pylibrb` does not expose `set_keyframe_map` (its `std::map` binding is missing).
  - The real-time engine has a ratio-dependent latency (~1.3k to 16k samples), so it cannot place beats sample-accurately.
  - Offline mode with a key-frame map is exact, so `warp.py` uses the CLI's `--timemap` (portable, no build step), with a ctypes fallback to `librubberband`. `pylibrb` is not used.
- **Analyzer: own grid fit** (envelope comb search + least squares + sample-level refinement) works for steady tempo and beats librosa's `beat_track` for this purpose. Keep it pluggable; try `beat_this` for downbeats on real material.
- **Tempo glide:** both tracks are mapped beat-for-beat to a master grid whose period glides linearly from A's to B's over the overlap. This keeps beats locked throughout.

## Open issues / not yet proven

- **No real music was tested.** The exit criterion ("sounds like a DJ mix on 3+ real pairs") needs real
  tracks and ears. Run `blend.py` on yours and listen.
- Downbeat confidence is low even on synthetic data (best/second-best ratio 1.04-1.19). Needs a better
  downbeat model and a manual override in the GUI.
- Mixes can exceed 0 dBFS when summing two loud tracks (the spike just scales the peak): a real limiter and loudness stage is needed (the spike only scales).
- The CLI backend writes temp wav files (fine for the spike; the real engine should stream or use the library).
- Phrase origin is the first detected downbeat; real tracks with silent intros or pickup bars will need a better origin.
