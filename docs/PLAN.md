# Blendscape – Concept & Plan

Automatic DJ-style blending of an ordered list of `.wav` files with a user-defined overlap.
This is a blend, not a crossfade: tracks are beat-matched, phase-aligned and mixed per
frequency band.

## Decisions so far

| Topic | Decision |
|---|---|
| Interface | Desktop/web GUI from the start (local web app) |
| Language | Python backend |
| Blend depth (v1) | Beatmatch + phase align + 3-band EQ swap + smart mix points |
| Material | Electronic/dance, steady tempo |
| Output | One mixed `.wav` (plus an editable mix plan) |
| License | GPL-3.0-or-later (`LICENSE.txt`); allows use of GPL libraries such as Rubber Band |

Deferred past v1: key matching / pitch shift, filter sweeps and effects, variable-tempo or
live material, MP3/FLAC input.

## What "a blend" means here

For each adjacent pair (A -> B):

1. **Mix points.** Choose where A starts leaving (`A.out`) and where B enters (`B.in`).
   Both snap to bar/phrase boundaries on the beat grid.
2. **Tempo match.** Time-stretch so beats coincide through the overlap.
   Ramp A's tempo toward B's BPM over the overlap; B plays at its native tempo afterwards.
   Stretch is capped (about +/-8%); beyond that, use half/double-time matching or warn.
3. **Phase align.** B's downbeat lands exactly on a downbeat of A. Sample-accurate placement.
4. **Frequency-aware mix.** Split both tracks into low / mid / high bands (Linkwitz-Riley
   crossovers). Bass swaps from A to B over a short window (1-2 bars) so only one kick/bassline
   is audible. Mids and highs follow their own, longer curves.
5. **Overlap length.** User-defined in bars/beats (default 16 beats), set globally and
   overridable per transition. Seconds shown for reference.

## Smart mix points

Per track, analysis produces a per-bar profile: RMS energy, low-band energy (kick/bass
presence) and spectral novelty. From that, label sections (intro / body / breakdown / outro)
on a phrase grid (8/16/32 bars from the downbeat).

Per transition, candidate `(A.out, B.in)` pairs are scored by:

- both on phrase boundaries (hard constraint)
- A.out inside A's outro, or at a breakdown/drop boundary
- B.in at the start of B's intro, or at a drop/body onset
- bass-clash penalty, from the low-band energy overlap across the planned overlap
- BPM distance (smaller stretch is better)
- fit with the requested overlap length

The top candidate is the default; the GUI shows alternatives. Always user-overridable.

## Architecture

```
wav files ──> analyze ──> plan ──> render ──> mix.wav
               │            │         ▲
               │            ▼         │
               │        mix_plan.json ┘   (human-editable, versioned)
               ▼
        analysis cache (per file hash)
```

- **analyze:** BPM, beat times, downbeats, per-bar energy profile, sections. Cached on disk
  by file hash. Manual overrides (BPM, downbeat offset) are stored with the project.
- **plan:** produces `mix_plan.json`: ordered tracks plus, per transition, `out_point`,
  `in_point`, `overlap_beats`, tempo curve, EQ curves. Pure data, no audio.
- **render:** executes the plan (stretch, band split, curves, sum, normalise/limit, write).
  Also renders short transition previews for the GUI.
- **Fallback:** if analysis confidence is low, use an equal-power crossfade and flag the
  transition in the GUI.

### Components

- `blendscape/` Python package: `analysis/`, `planning/`, `render/`, `model/` (plan schema).
- `blendscape.api`: FastAPI service (local) exposing projects, analysis jobs, plan edits,
  preview render, final render; WebSocket/SSE for progress.
- `web/`: frontend (React + Vite + TypeScript). Wavesurfer-style waveform with beat grid,
  track list with drag-reorder, per-transition editor (overlap, mix points, EQ curve),
  preview playback of a transition, export.
- Packaging: `blendscape serve` opens the local app in the browser first; a desktop wrapper
  (pywebview or Tauri) can come later without changing the architecture.
- CLI: `blendscape render plan.json -o mix.wav` for headless and testing use.

### Library choices (to confirm in the spike)

| Need | Candidate | Notes |
|---|---|---|
| Decode/encode wav | `soundfile` | |
| Beats/tempo | `librosa`; consider `beat_this` for better downbeats | beat_this pulls in torch, so keep the analyzer pluggable |
| Time-stretch | Rubber Band (`pyrubberband` / `rubberband` CLI) | needs R3 engine for quality, check licensing |
| Band split / EQ | `scipy.signal` (LR4 crossovers) | zero-phase not needed, LR4 sums flat |
| API | FastAPI + uvicorn | |
| Frontend | React + Vite + TS, `wavesurfer.js` | |

## Milestones

Status: **0 spike, 1 core engine and CLI, 2 smart mix points done**; the limiter and the downbeat model (milestone 5 items) are done; 3-5 to do.

0. **Spike (de-risk). Done** (`spike/`, superseded by `src/blendscape`). Notebook/script: take two dance tracks, detect beats + downbeats,
   stretch, align, 3-band swap, listen. Decide analyzer and stretcher. Exit: a blend that
   sounds like a DJ mix on 3+ track pairs.
1. **Core engine + CLI. Done.** `analyze`, hand-written `mix_plan.json`, `render`. Unit tests on
   grid math and alignment (synthetic click tracks with known BPM/offset).
2. **Planner + smart mix points. Done.** Section detection, candidate scoring, defaults from the
   user's overlap setting.
3. **API.** Projects, jobs, plan CRUD, preview/final render.
4. **GUI.** Track list, waveform/beat grid, transition editor, preview, export.
5. **Hardening.** Low-confidence handling, big-BPM-gap handling, loudness normalisation,
   performance (long tracks, caching), packaging.

### Milestone 2 notes

- Per-bar low/mid/high loudness -> novelty-based boundaries, snapped to the 8-bar phrase grid -> labelled by
  loudness relative to the loud parts of the track (`src/blendscape/sections.py`).
- The planner (`src/blendscape/planner.py`) scores every phrase-aligned (out, in) pair on: outgoing section, incoming
  section (and whether the drop lands as the overlap ends), bass clash, and how much of each track is played. Weights
  are constants at the top of the file and still need tuning on real music.
- Known limits: gradual fade-outs are labelled as outro late; mixes are chosen greedily transition by transition (no
  look-ahead beyond leaving the next track room to exit); a track with no clear structure falls back to "leave
  late, enter at the start"; the section labels assume club-style 4/4 arrangements.

### Limiter notes

- `src/blendscape/limiter.py`: offline look-ahead true-peak limiter (stereo-linked, 4x oversampled peak detection per
  ITU-R BS.1770). Gain = sliding minimum of the required gain, smoothed over the same window (guarantees the ceiling,
  gives a smooth attack), then an exponential release applied to the reduction (only ever lowers the gain).
- The ceiling holds exactly at the 4x measurement. A 4x meter can under-read wideband noise: the 8x+ peak of white
  noise sat 0.34 dB over, band-limited material 0.04 dB. For deliverables that matter, use `--ceiling-db -1` or lower.
- Not done: loudness normalisation (the limiter does not raise level), dither when exporting 16-bit.

### Downbeat model notes

- `src/blendscape/downbeat.py` replaces the single mid-band accent rule with six independent cues (kick accent, mid
  accent, backbeat claps, chord change, bass-root change, impacts, arrangement boundaries), fused into a posterior
  over the four candidate beats. Confidence is that posterior (0.25 = no idea).
- `benchmarks/downbeat_bench.py` measures it on `tests/synth_rich.py`, a generator of club-style tracks with the bar
  line hidden in different places (and two hard styles: no cue at all; accents pointing at the wrong beat). With the
  true beat grid supplied, on seeds not used for tuning: old rule 48-52%, new 88-92%; every cued style 100% on the
  last seed; confident predictions (>=50%) were all right; tracks with no cue got 0.25-0.43 confidence.
- **These are synthetic tracks written by the same person who wrote the detector.** Treat the percentages as "the
  cues work as designed", not as a measured accuracy on music. The weights and temperature were set by hand, not
  fitted. Real music is the test: `blendscape click` and `--downbeat` exist so a wrong guess costs ten seconds.
- Not covered: non-4/4 material, tracks with tempo changes, genres with no kick or bass (the cues assume club music).

## Testing approach

- Synthetic fixtures: click/kick tracks with known BPM and offset, to assert sample-level
  alignment and tempo-ramp accuracy.
- Objective checks on rendered blends: no clipping, continuous output (no discontinuities at
  joins), bass-band energy never doubles during the swap.
- A small set of real, licensed/royalty-free dance tracks for listening tests (not in git if large).

## Risks and open questions

- Downbeat detection is the weakest link in the analysis. Needs manual override in the GUI
  and visible confidence.
- Time-stretch quality (Rubber Band is GPL; fine under our license).
- Section detection heuristics may need tuning per sub-genre.
- Input sample rates/bit depths differ: resample to a common project rate (e.g. 44.1 kHz),
  float32 internally; output bit depth as a setting.
- Preview latency: render only the transition window +/- a few bars.
- Licensing: the project is GPL-3.0-or-later. Every dependency must be GPL-compatible; check this when adding one.
  New source files carry an SPDX header: `SPDX-License-Identifier: GPL-3.0-or-later`.
