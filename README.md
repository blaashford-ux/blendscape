# Blendscape

Project description goes here.

## Branching

- `main` – stable, release-ready code
- `dev` – integration branch; feature branches are cut from and merged back into `dev`

## Getting started

Needs Python 3.10+ and the [Rubber Band](https://breakfastquay.com/rubberband/) command-line tool (v3+; used for
time-stretching). On Linux/macOS install it with your package manager (`apt install rubberband-cli`,
`brew install rubberband`). On Windows download the executable zip, unzip it, and either put its folder on `PATH` or
set `RUBBERBAND` to the full path of `rubberband.exe`.

```bash
python -m venv .venv
. .venv/bin/activate              # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

### Use

```bash
blendscape analyze track1.wav track2.wav                 # BPM, beat grid, downbeat
blendscape mix one.wav two.wav three.wav -o mix.wav      # analyse, plan and render in one go
blendscape mix one.wav two.wav -o mix.wav --overlap-beats 16 --save-plan plan.json

# or in steps, so you can inspect and edit the plan:
blendscape plan one.wav two.wav three.wav -o plan.json --overlap-beats 32
blendscape validate plan.json                            # where each transition lands, plus any problems
blendscape render plan.json -o mix.wav --bit-depth 24
```

Tracks are blended in the order given. Each transition beat-matches the two tracks (the tempo glides from the
outgoing track's BPM to the incoming one's over the overlap), lines up their downbeats, and swaps the bass, mids and
highs at different moments. Options: `--overlap-beats` (default 32 = 8 bars), `--style bass_swap|crossfade`,
`--phrase-bars` (default 8), `--sample-rate`, `--bit-depth 16|24|32`, `--ceiling-db`.

### The plan file

`plan.json` is plain JSON and is the thing to edit. Beat numbers refer to each *source* track's own beat grid:

- `tracks[]`: `bpm`, `offset_s` (time of beat 0) and `downbeat` (0-3, which beat starts a bar) come from analysis;
  correct them by hand if detection got a track wrong.
- `transitions[]`: `out_beat` (where the overlap starts in the outgoing track), `in_beat` (the incoming track's beat
  that lands there), `overlap_beats`, `style`, and `eq` (when each band fades, as fractions of the overlap).

`blendscape validate` checks the plan and prints the mix times. Analysis results are cached by file content in
`~/.cache/blendscape` (override with `$BLENDSCAPE_CACHE`).

### Develop

```bash
ruff check .
pytest                              # ~2 min; the end-to-end renders are marked `slow`
BLENDSCAPE_SKIP_SLOW=1 pytest       # quick run without the renders
```

See [docs/PLAN.md](docs/PLAN.md) for the design and milestones.

## License

Blendscape is free software, licensed under the
[GNU General Public License v3.0 or later](LICENSE.txt) (`GPL-3.0-or-later`).
