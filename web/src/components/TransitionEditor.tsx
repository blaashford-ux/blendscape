import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api/client";
import type { EqCurves, Project, Style, Transition, TransitionPatch } from "../api/types";
import { BEATS_PER_BAR, barOf, beatTime, fmtTime, overlapBeatsForSeconds, snapBeat, snapOverlap, type Grid } from "../lib/timeline";
import type { JobRun } from "../state/useJobRunner";
import { usePlayer } from "../state/usePlayer";
import { EqGraph } from "./EqGraph";
import { Waveform, type MarkerDrag } from "./Waveform";
import { usePeaks } from "../state/useProject";

interface Props {
  project: Project;
  index: number;
  onProject: (p: Project) => void;
  preview: { run: JobRun | null; onStart: () => void; onCancel: () => void };
}

const SPAN_BEFORE = 30;
const SPAN_AFTER = 12;

/** Edit one transition: where it leaves and enters, how long it lasts, how it blends; and listen to it. */
export function TransitionEditor({ project, index, onProject, preview }: Props) {
  const plan = project.plan!;
  const tr = plan.transitions[index];
  const a = plan.tracks[index];
  const b = plan.tracks[index + 1];
  const player = usePlayer();
  const peaksA = usePeaks(project.id, a?.id);
  const peaksB = usePeaks(project.id, b?.id);
  const [error, setError] = useState<{ message: string; errors: string[] } | null>(null);
  const [busy, setBusy] = useState(false);
  const phraseBeats = project.settings.phrase_bars * BEATS_PER_BAR;
  // while a marker is being dragged, what the plan would be if it were dropped now
  const [drag, setDrag] = useState<TransitionPatch>({});

  useEffect(() => {
    setError(null);
    setDrag({});
  }, [index]);

  const call = async (fn: () => Promise<Project>) => {
    setBusy(true);
    setError(null);
    try {
      onProject(await fn());
    } catch (e) {
      const err = e as Error & { errors?: string[] };
      setError({ message: err.message, errors: err.errors ?? [] });
    } finally {
      setBusy(false);
    }
  };
  const patch = (p: TransitionPatch) => call(() => api.patchTransition(project.id, index, p));

  const gridA: (Grid & { phraseBars: number }) | null = useMemo(
    () => (a ? { offset_s: a.offset_s, bpm: a.bpm, downbeat: a.downbeat, phraseBars: project.settings.phrase_bars } : null),
    [a, project.settings.phrase_bars],
  );
  const gridB = useMemo(
    () => (b ? { offset_s: b.offset_s, bpm: b.bpm, downbeat: b.downbeat, phraseBars: project.settings.phrase_bars } : null),
    [b, project.settings.phrase_bars],
  );
  if (!tr || !a || !b || !gridA || !gridB) return null;

  const outBeat = drag.out_beat ?? tr.out_beat;
  const inBeat = drag.in_beat ?? tr.in_beat;
  const overlapBeats = drag.overlap_beats ?? tr.overlap_beats;
  const outTime = beatTime(a, outBeat);
  const inTime = beatTime(b, inBeat);
  const endA = beatTime(a, outBeat + overlapBeats); // where the overlap ends in each file
  const endB = beatTime(b, inBeat + overlapBeats);
  // the view is laid out from the saved plan, so it holds still while you drag
  const span =
    Math.max(beatTime(a, tr.out_beat + tr.overlap_beats) - tr.out_time_s, beatTime(b, tr.in_beat + tr.overlap_beats) - tr.in_time_s) +
    SPAN_BEFORE +
    SPAN_AFTER;
  const dragging = Object.keys(drag).length > 0;

  /** A marker is being dragged (or dropped): snap to bars (beats with Shift), preview it, and save on drop. */
  const onDrag = (d: MarkerDrag) => {
    const step = d.fine ? 1 : BEATS_PER_BAR;
    let next: TransitionPatch;
    if (d.id === "out") next = { out_beat: snapBeat(a, d.t, step) };
    else if (d.id === "in") next = { in_beat: snapBeat(b, d.t, step) };
    else if (d.id === "end-a") next = { overlap_beats: snapOverlap(a, outBeat, d.t, step) };
    else next = { overlap_beats: snapOverlap(b, inBeat, d.t, step) };
    if (d.phase === "move") return setDrag(next);
    if (d.phase === "cancel") return setDrag({});
    const [key, value] = Object.entries(next)[0] as [keyof TransitionPatch, number];
    setDrag(next);
    if (tr[key as "out_beat" | "in_beat" | "overlap_beats"] === value) return setDrag({});
    void patch(next).then(() => setDrag({}));
  };
  const a0 = tr.out_time_s - SPAN_BEFORE;
  const b0 = tr.in_time_s - 10;
  const OVERLAP = "rgba(255, 200, 80, 0.16)";
  const audition = (id: string, name: string) => (t: number) => player.play(api.audioUrl(project.id, id), `${name} (original)`, { at: Math.max(0, t) });

  const cut = tr.unplayed_tail_s;
  const trackWarnings = tr.warnings;
  const previewing = preview.run?.state === "running";

  return (
    <section className="panel transition-editor" aria-label={`Transition ${index + 1}`}>
      <div className="panel-head">
        <h2>
          Transition {index + 1}: <span className="nowrap">{a.name}</span> → <span className="nowrap">{b.name}</span>
        </h2>
        {tr.score !== null && <span className="chip" title="The planner's score for this choice">score {tr.score.toFixed(2)}</span>}
        {tr.edited && <span className="chip edited" title="You changed this transition by hand">edited</span>}
        <span className="muted small">
          mix {fmtTime(tr.start_s)}–{fmtTime(tr.end_s)} · {tr.overlap_s.toFixed(1)} s overlap
        </span>
        <button
          className="reset-btn"
          disabled={busy || !tr.edited}
          onClick={() => call(() => api.resetTransition(project.id, index))}
          title={tr.edited ? "Forget your changes and go back to what the planner first suggested" : "Nothing to reset: this is the planner's choice"}
        >
          Reset to the planner's choice
        </button>
      </div>
      {tr.note && <p className="note small">{tr.note}</p>}
      {trackWarnings.map((w) => (
        <p key={w} className="warn small">
          ⚠ {w}
        </p>
      ))}

      <div className="strips">
        <div className="strip">
          <div className="strip-label small">
            <strong>{a.name}</strong> leaves here
            <span className="muted"> (click the waveform to hear the original from that point)</span>
          </div>
          <Waveform
            peaks={peaksA}
            t0={a0}
            t1={a0 + span}
            height={92}
            color="#7cc4ff"
            grid={gridA}
            sections={a.sections}
            regions={[{ t0: outTime, t1: endA, color: OVERLAP }]}
            markers={[
              { id: "out", t: outTime, color: "#ffcc4d", label: "leaves", draggable: true },
              { id: "end-a", t: endA, color: "#ff7a7a", label: "overlap ends", draggable: true },
            ]}
            onMarkerDrag={onDrag}
            onSeek={audition(a.id, a.name)}
            label={`${a.name} around the transition`}
          />
        </div>
        <div className="strip">
          <div className="strip-label small">
            <strong>{b.name}</strong> comes in here
          </div>
          <Waveform
            peaks={peaksB}
            t0={b0}
            t1={b0 + span}
            height={92}
            color="#ff9f6b"
            grid={gridB}
            sections={b.sections}
            regions={[{ t0: inTime, t1: endB, color: OVERLAP }]}
            markers={[
              { id: "in", t: inTime, color: "#ffcc4d", label: "enters", draggable: true },
              { id: "end-b", t: endB, color: "#ff7a7a", label: "overlap ends", draggable: true },
            ]}
            onMarkerDrag={onDrag}
            onSeek={audition(b.id, b.name)}
            label={`${b.name} around the transition`}
          />
        </div>
      </div>

      <p className="small drag-hint muted" aria-live="polite">
        {dragging
          ? `${a.name} leaves at ${fmtTime(outTime)} · ${b.name} enters at ${fmtTime(inTime)} · overlap ${overlapBeats} beats (${overlapBeats / BEATS_PER_BAR} bars)`
          : "Drag the yellow and red markers to move them: they snap to bars; hold Shift to snap to single beats."}
      </p>

      <div className="controls">
        <PointControl
          title={`${a.name} leaves at`}
          beat={tr.out_beat}
          grid={a}
          phraseBeats={phraseBeats}
          disabled={busy}
          onChange={(beat) => patch({ out_beat: beat })}
          idPrefix="out"
        />
        <PointControl
          title={`${b.name} enters at`}
          beat={tr.in_beat}
          grid={b}
          phraseBeats={phraseBeats}
          disabled={busy}
          onChange={(beat) => patch({ in_beat: beat })}
          idPrefix="in"
        />
        <OverlapControl tr={tr} bpm={a.bpm} disabled={busy} onChange={(n) => patch({ overlap_beats: n })} />
        <fieldset className="control">
          <legend>Blend style</legend>
          {(["bass_swap", "crossfade"] as Style[]).map((s) => (
            <label key={s} className="radio">
              <input type="radio" name={`style-${index}`} checked={tr.style === s} disabled={busy} onChange={() => patch({ style: s })} />
              {s === "bass_swap" ? "Bass swap (DJ-style)" : "Simple crossfade"}
            </label>
          ))}
          <p className="hint small">
            {tr.style === "bass_swap"
              ? "Beats are matched; the bass, mids and highs trade places at different moments."
              : "No tempo matching: a plain equal-power fade. Often better for songs with vocals."}
          </p>
        </fieldset>
      </div>

      <p className={"small " + (cut > 1 ? "warn" : "muted")}>
        {cut > 1 ? `The last ${cut.toFixed(1)} s of ${a.name} is not played.` : `${a.name} plays to its end.`}
      </p>

      {tr.style === "bass_swap" && <EqEditor eq={tr.eq} beats={tr.overlap_beats} style={tr.style} disabled={busy} onChange={(eq) => patch({ eq })} />}

      {tr.alternatives.length > 0 && (
        <details className="alternatives">
          <summary>Other places the planner considered ({tr.alternatives.length})</summary>
          <table>
            <thead>
              <tr>
                <th>score</th>
                <th>{a.name} leaves</th>
                <th>{b.name} enters</th>
                <th>why</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {tr.alternatives.map((alt, i) => (
                <tr key={`${alt.out_beat}-${alt.in_beat}`}>
                  <td>{alt.score.toFixed(2)}</td>
                  <td>{fmtTime(alt.out_time_s)}</td>
                  <td>{fmtTime(alt.in_time_s)}</td>
                  <td className="small">{alt.note}</td>
                  <td>
                    <button disabled={busy} onClick={() => call(() => api.useAlternative(project.id, index, i))} aria-label={`Use alternative ${i + 1}`}>
                      Use
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}

      {error && (
        <div className="error-box" role="alert">
          <strong>{error.message}</strong>
          {error.errors.length > 1 && (
            <ul>
              {error.errors.map((e) => (
                <li key={e}>{e}</li>
              ))}
            </ul>
          )}
        </div>
      )}

      <div className="actions">
        <button className="primary" disabled={busy || previewing} onClick={preview.onStart}>
          ▶ Preview this transition
        </button>
        {previewing && <button onClick={preview.onCancel}>Cancel preview</button>}
      </div>
    </section>
  );
}

/** Where a track leaves or enters: shows the time and bar number, with buttons that move it by bars or phrases. */
function PointControl({
  title,
  beat,
  grid,
  phraseBeats,
  disabled,
  onChange,
  idPrefix,
}: {
  title: string;
  beat: number;
  grid: Grid;
  phraseBeats: number;
  disabled: boolean;
  onChange: (beat: number) => void;
  idPrefix: string;
}) {
  const bar = barOf(grid, beat);
  const step = (delta: number) => onChange(Math.max(0, beat + delta));
  return (
    <fieldset className="control" aria-label={title}>
      <legend>{title}</legend>
      <div className="point-value">
        <strong data-testid={`${idPrefix}-time`}>{fmtTime(beatTime(grid, beat))}</strong>
        <span className="muted small"> bar {Number.isInteger(bar) ? bar + 1 : bar.toFixed(2)}</span>
      </div>
      <div className="nudges">
        <button disabled={disabled} onClick={() => step(-phraseBeats)} aria-label={`${title} one phrase earlier`}>
          ◀◀ phrase
        </button>
        <button disabled={disabled} onClick={() => step(-BEATS_PER_BAR)} aria-label={`${title} one bar earlier`}>
          ◀ bar
        </button>
        <button disabled={disabled} onClick={() => step(+BEATS_PER_BAR)} aria-label={`${title} one bar later`}>
          bar ▶
        </button>
        <button disabled={disabled} onClick={() => step(+phraseBeats)} aria-label={`${title} one phrase later`}>
          phrase ▶▶
        </button>
      </div>
    </fieldset>
  );
}

function OverlapControl({ tr, bpm, disabled, onChange }: { tr: Transition; bpm: number; disabled: boolean; onChange: (beats: number) => void }) {
  const [seconds, setSeconds] = useState("");
  const presets = [8, 16, 32, 64];
  return (
    <fieldset className="control">
      <legend>Overlap</legend>
      <div className="point-value">
        <strong data-testid="overlap-beats">{tr.overlap_beats} beats</strong>
        <span className="muted small"> = {tr.overlap_s.toFixed(1)} s ({tr.overlap_beats / BEATS_PER_BAR} bars)</span>
      </div>
      <div className="nudges">
        <button disabled={disabled || tr.overlap_beats <= 4} onClick={() => onChange(tr.overlap_beats - BEATS_PER_BAR)} aria-label="Shorten the overlap by one bar">
          − bar
        </button>
        <button disabled={disabled} onClick={() => onChange(tr.overlap_beats + BEATS_PER_BAR)} aria-label="Lengthen the overlap by one bar">
          + bar
        </button>
        {presets.map((n) => (
          <button key={n} disabled={disabled} className={tr.overlap_beats === n ? "on" : ""} onClick={() => onChange(n)}>
            {n}
          </button>
        ))}
      </div>
      <form
        className="seconds-form small"
        onSubmit={(e) => {
          e.preventDefault();
          const s = Number(seconds);
          if (s > 0) {
            onChange(overlapBeatsForSeconds(s, bpm));
            setSeconds("");
          }
        }}
      >
        <label>
          or about
          <input
            type="number"
            min={1}
            step={0.5}
            value={seconds}
            onChange={(e) => setSeconds(e.target.value)}
            aria-label="Overlap in seconds"
            placeholder="8"
            disabled={disabled}
          />
          seconds
        </label>
        <button type="submit" disabled={disabled || !(Number(seconds) > 0)}>
          Set
        </button>
      </form>
    </fieldset>
  );
}

const RANGES: { key: "mid_in" | "mid_out" | "hi_in" | "hi_out"; label: string }[] = [
  { key: "mid_in", label: "Mids come in" },
  { key: "mid_out", label: "Mids go out" },
  { key: "hi_in", label: "Highs come in" },
  { key: "hi_out", label: "Highs go out" },
];

/** Fine control over when each frequency band fades. Changes are sent after a short pause. */
function EqEditor({ eq, beats, style, disabled, onChange }: { eq: EqCurves; beats: number; style: Style; disabled: boolean; onChange: (eq: Partial<EqCurves>) => void }) {
  const [draft, setDraft] = useState<EqCurves>(eq);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const serverKey = JSON.stringify(eq);
  useEffect(() => setDraft(eq), [serverKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const set = (next: EqCurves, changed: Partial<EqCurves>) => {
    setDraft(next);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => onChange(changed), 400);
  };
  useEffect(() => () => void (timer.current && clearTimeout(timer.current)), []);

  const range = (key: (typeof RANGES)[number]["key"], which: 0 | 1, v: number) => {
    const cur = [...draft[key]] as [number, number];
    cur[which] = v;
    if (cur[0] >= cur[1]) {
      if (which === 0) cur[0] = Math.max(0, cur[1] - 0.02);
      else cur[1] = Math.min(1, cur[0] + 0.02);
    }
    set({ ...draft, [key]: cur }, { [key]: cur });
  };

  return (
    <details className="eq-editor">
      <summary>Fine-tune the frequency swap</summary>
      <div className="eq-body">
        <EqGraph eq={draft} style={style} beats={beats} />
        <div className="sliders">
          <label>
            Bass swaps at {Math.round(draft.bass_swap_center * 100)}% of the overlap
            <input
              type="range"
              min={0.1}
              max={0.9}
              step={0.01}
              value={draft.bass_swap_center}
              disabled={disabled}
              onChange={(e) => set({ ...draft, bass_swap_center: Number(e.target.value) }, { bass_swap_center: Number(e.target.value) })}
            />
          </label>
          <label>
            …taking {draft.bass_swap_beats.toFixed(0)} beats
            <input
              type="range"
              min={1}
              max={16}
              step={1}
              value={draft.bass_swap_beats}
              disabled={disabled}
              onChange={(e) => set({ ...draft, bass_swap_beats: Number(e.target.value) }, { bass_swap_beats: Number(e.target.value) })}
            />
          </label>
          {RANGES.map((r) => (
            <div key={r.key} className="range-pair">
              <span>{r.label}</span>
              <input type="range" min={0} max={1} step={0.01} value={draft[r.key][0]} disabled={disabled} onChange={(e) => range(r.key, 0, Number(e.target.value))} aria-label={`${r.label}: start`} />
              <input type="range" min={0} max={1} step={0.01} value={draft[r.key][1]} disabled={disabled} onChange={(e) => range(r.key, 1, Number(e.target.value))} aria-label={`${r.label}: end`} />
            </div>
          ))}
        </div>
      </div>
    </details>
  );
}
