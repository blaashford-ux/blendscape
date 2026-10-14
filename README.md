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

Tracks are blended in the order given. Where each transition happens is chosen from the songs' structure: `analyze`
lists the detected sections (intro / body / breakdown / outro), and the planner scores every phrase-aligned pair of
leave/enter points. It prefers leaving from an outro (or into a breakdown) late in the outgoing track, entering through
the incoming track's intro with its drop landing as the overlap ends, and little bass in both tracks at once.
`--explain` lists the runner-up choices; `--mix-points latest` turns the scoring off and just leaves on the last phrase
that fits.

**How much of a song is played.** The outgoing track runs to the end of the overlap and what comes after is dropped,
so the planner limits that: by default at most 10 seconds of the outgoing track's music may go unplayed
(`--max-cut-seconds`, 0 = no limit). If the 8-bar phrase grid can't manage that, it tries 4-, 2- and 1-bar boundaries
and says so in the plan notes. Trailing silence and very quiet fades don't count as music. The plan summary always
shows what is skipped ("the last 3.2 s of t1-song is not played") and the overlap length in seconds.

**How long the overlap is.** `--overlap-beats` (default 32, about 15 s at 130 BPM) or `--overlap-seconds 8`, which
picks the whole number of bars closest to that time at the outgoing track's tempo. Songs with vocals usually sound
better with a shorter overlap (5-10 s) so two voices aren't on top of each other for long.

Each transition Each transition beat-matches the two tracks (the tempo glides from the
outgoing track's BPM to the incoming one's over the overlap), lines up their downbeats, and swaps the bass, mids and
highs at different moments. Options: `--overlap-beats` (default 32 = 8 bars), `--style bass_swap|crossfade`,
`--phrase-bars` (default 8), `--sample-rate`, `--bit-depth 16|24|32`.

The mix goes through a look-ahead true-peak limiter, so two loud tracks summed in an overlap never clip. It only turns
the level down at the peaks (smooth 3 ms attack, 120 ms release) and leaves everything else alone. `--ceiling-db`
(default -1), `--release-ms`, `--sample-peak` (skip the inter-sample check) and `--no-limiter` adjust it; the same
settings live in the plan's `limiter` block. Peak levels before and after are printed when rendering.

### Checking the bar lines by ear

A blend only sounds right if both tracks' bar lines are right, and that is the one thing detection can get wrong
without any sign in the numbers. `analyze` prints how sure it is (`downbeat beat 2 (87% sure)`), and below about 50%
it tells you to check:

```bash
blendscape click one.wav -o check.wav          # 30 s with a metronome: a HIGH click on the detected bar line
```

Play `check.wav` and count along with the music. The high click should land on the "one" of each bar (where chords
change, a crash or a drop hits). The command prints which `--downbeat` value to use if it lands on the 2, 3 or 4. Then
force it, for any file in a `plan`/`mix` run:

```bash
blendscape mix one.wav two.wav -o mix.wav --downbeat two=3     # NAME is the file name, stem or full path
```

If the clicks drift away from the kick, the tempo or beat grid is off, which is a different problem (tell me).

### The plan file

`plan.json` is plain JSON and is the thing to edit. Beat numbers refer to each *source* track's own beat grid:

- `tracks[]`: `bpm`, `offset_s` (time of beat 0) and `downbeat` (0-3, which beat starts a bar) come from analysis;
  correct them by hand if detection got a track wrong.
- `transitions[]`: `out_beat` (where the overlap starts in the outgoing track), `in_beat` (the incoming track's beat
  that lands there), `overlap_beats`, `style`, and `eq` (when each band fades, as fractions of the overlap). The planner
  also records `score`, `note` (why it chose this) and `alternatives` (runners-up you can swap in); these are
  informational and ignored when rendering.
- `tracks[].sections`: the detected structure, informational.

`blendscape validate` checks the plan and prints the mix times. Analysis results are cached by file content in
`~/.cache/blendscape` (override with `$BLENDSCAPE_CACHE`).

### Web UI

```bash
pip install -e ".[api]"
cd web && npm install && npm run build && cd ..      # needs Node 20+; builds web/dist once
blendscape serve --open                              # http://127.0.0.1:8765
```

Open the page, name a mix, drop in your songs (or add them by file path), and it analyses them and lays out a plan:

- **The mix**: every track on the output timeline with the overlaps shaded. Click an overlap to edit that transition.
- **Transition editor**: both songs zoomed around the mix point, with their beat grid and sections (intro / body /
  breakdown / outro). Move where the first leaves and the second enters by a bar or a phrase, set the overlap, choose
  bass-swap or plain crossfade, fine-tune the frequency swap, try the planner's other choices, and **preview** just that
  transition before rendering anything.
- **Tracks**: drag to reorder; each shows how sure the bar line is, with buttons to shift it and a click-track to check
  it by ear.
- **Render the mix** when you're happy; the WAV is listed under "Rendered mixes" to play or download.

Everything the UI does goes through the local HTTP API ([docs/API.md](docs/API.md), interactive reference at `/docs`);
it listens on this computer only unless you say otherwise. For working on the UI itself: `cd web && npm run dev`
(proxies to a running `blendscape serve`).

### Develop

```bash
ruff check .
pytest                              # ~7 min; the end-to-end renders are marked `slow`
BLENDSCAPE_SKIP_SLOW=1 pytest       # quicker run without the renders
cd web && npm test                  # UI unit/component tests (a few seconds)
cd web && npm run e2e               # real browser + real server (needs `npm run build` first; ~1 min)
```

See [docs/PLAN.md](docs/PLAN.md) for the design and milestones.

## License

Blendscape is free software, licensed under the
[GNU General Public License v3.0 or later](LICENSE.txt) (`GPL-3.0-or-later`).
