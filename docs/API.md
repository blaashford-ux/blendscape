# Blendscape local API

`blendscape serve` starts a local HTTP API (default `http://127.0.0.1:8765`; interactive reference at `/docs`, schema at
`/openapi.json`). It is what the web UI (milestone 4) talks to, and you can script it directly.

**Local only.** It trusts whoever can reach it: it reads audio from any path you give it and writes inside its data
folder. It listens on `127.0.0.1` by default; `--host` something else prints a warning. CORS allows only
`localhost` / `127.0.0.1` origins.

## Concepts

- A **project** is an ordered list of tracks, settings, the current plan and your manual edits. Stored on disk in
  `~/.local/share/blendscape/projects/<id>/` (override with `--home` or `$BLENDSCAPE_HOME`), so it survives restarts.
- Long work (**analysis, previews, final renders**) runs as a **job**. Starting one returns `202` and a job; follow it
  by polling `GET /api/jobs/{id}` or streaming `GET /api/jobs/{id}/events` (server-sent events). Jobs can be cancelled;
  that takes effect at the next progress step, not instantly.
- The **plan** is derived from the tracks and settings, so it goes **stale** when you add/remove/reorder tracks, change
  settings or a downbeat. Planning again (`POST .../plan`) re-applies your hand edits to individual transitions wherever
  they still fit and reports the ones it had to drop in `plan_warnings`.
- Track ids (`t_xxxxxxxx`) are stable for the life of a track, so edits stay attached to a *pair* of tracks when you
  reorder.

## Typical session

```
POST   /api/projects                         {"name": "Friday set"}              -> project
POST   /api/projects/{p}/tracks              {"path": "/music/one.wav"}          -> track   (or .../tracks/upload)
PATCH  /api/projects/{p}/settings            {"overlap_seconds": 8}
POST   /api/projects/{p}/analyze             -> job; its result says planned: true
GET    /api/projects/{p}                     -> tracks, settings, plan (times, warnings, alternatives)
PATCH  /api/projects/{p}/plan/transitions/0  {"out_beat": 128, "overlap_beats": 16}
POST   /api/projects/{p}/plan/transitions/0/preview   {"lead_s": 8, "tail_s": 8} -> job; result.url is a WAV
POST   /api/projects/{p}/render              {"name": "friday"}                  -> job; result.url is the mix
```

## Endpoints

| | |
|---|---|
| `GET /api/health` | version, which Rubber Band backend is in use (`null` = renders will 503) |
| `GET/POST /api/projects`, `GET/PATCH/DELETE /api/projects/{p}` | list, create, read, rename, delete |
| `PATCH /api/projects/{p}/settings` | any subset of: `overlap_beats`, `overlap_seconds` (`clear_overlap_seconds: true` to unset), `style`, `phrase_bars`, `mix_points`, `max_cut_seconds`, `sample_rate`, `bit_depth`, `ceiling_db`, `limiter{enabled,true_peak,lookahead_ms,release_ms}` |
| `POST /api/projects/{p}/tracks` | add a file by `{"path"}`; read in place, never copied |
| `POST /api/projects/{p}/tracks/upload` | add a file by multipart upload (field `file`); a copy is kept in the project |
| `DELETE /api/projects/{p}/tracks/{t}` | remove (deletes an uploaded copy, never a path-added file) |
| `PUT /api/projects/{p}/tracks/order` | `{"order": [ids...]}`, every id exactly once |
| `PUT /api/projects/{p}/tracks/{t}/downbeat` | `{"downbeat": 0-3}` forces which grid beat starts a bar; `null` goes back to detected |
| `GET /api/projects/{p}/tracks/{t}/analysis` | full analysis (tempo, grid, per-bar loudness, sections, downbeat posterior) |
| `GET /api/projects/{p}/tracks/{t}/waveform?points=1500` | `{min[], max[], duration_s}` peaks for drawing a waveform |
| `GET /api/projects/{p}/tracks/{t}/audio` | the original file, with HTTP Range support for seeking |
| `POST /api/projects/{p}/tracks/{t}/click?start=&seconds=` | WAV with a metronome on the detected bar line, to check it by ear |
| `POST /api/projects/{p}/analyze` | job: analyse any track that needs it, then plan (`{"replan": false}` to skip, `{"force": true}` to redo) |
| `POST /api/projects/{p}/plan` | plan again now (needs every track analysed) |
| `GET /api/projects/{p}/plan` | the raw plan, staleness, warnings, and the described timeline |
| `PATCH /api/projects/{p}/plan/transitions/{i}` | `out_beat`, `in_beat`, `overlap_beats`, `style`, `eq{...}`; validated as a whole, refused (422) if it breaks the plan |
| `POST /api/projects/{p}/plan/transitions/{i}/alternative` | `{"index": n}` take one of the planner's runners-up |
| `DELETE /api/projects/{p}/plan/transitions/{i}/override` | forget your edits to this transition and let the planner choose |
| `POST /api/projects/{p}/plan/transitions/{i}/preview` | job: render just this transition; `result.url` |
| `POST /api/projects/{p}/render` | job: render the whole mix into the project; `result.url` |
| `GET /api/projects/{p}/previews/{name}`, `.../mixes/{name}` | the rendered WAVs (mixes download as attachments) |
| `GET /api/jobs`, `GET /api/jobs/{id}`, `DELETE /api/jobs/{id}` | list, status, cancel |
| `GET /api/jobs/{id}/events` | SSE stream of `progress` events: `{seq, state, message, fraction, result?, error?}`; past events are replayed first |

## What the plan looks like to a client

`GET /api/projects/{p}` includes `plan`:

```json
{ "tracks": [{"id","name","bpm","downbeat","confidence","duration_s","end_s","sections":[...]}],
  "transitions": [{
     "index": 0, "from": "t_..", "to": "t_..", "style": "bass_swap",
     "overlap_beats": 32, "overlap_s": 15.2, "out_beat": 192, "in_beat": 1,
     "out_time_s": 93.3, "in_time_s": 0.8,      // where it leaves / enters, in each source file
     "start_s": 93.3, "end_s": 108.5,           // where the overlap sits in the finished mix
     "unplayed_tail_s": 1.2, "tempo_gap": 0.032, "score": 0.88, "note": "...",
     "eq": {...}, "warnings": ["Tempo gap 12% ..."],
     "alternatives": [{"out_beat","in_beat","score","note","out_time_s","in_time_s"}] }],
  "total_duration_s": 292.1 }
```

## Errors

`404` unknown project/track/job/transition or file · `409` the project isn't in a state where that can be done (not
analysed yet, plan stale, no tracks) with a message saying what to do first · `422` bad input (not an audio file,
invalid plan edit; plan errors come back as `{"detail", "errors": [...]}`) · `503` rendering needs the `rubberband`
executable and it isn't installed.
