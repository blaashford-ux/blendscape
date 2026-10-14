import { useEffect, useMemo, useRef, useState } from "react";
import type { Project } from "../api/types";
import { fmtDuration, fmtTime, layoutLanes, type Lane } from "../lib/timeline";
import { rulerStep } from "../lib/waveform";
import { usePeaks } from "../state/useProject";
import { Waveform } from "./Waveform";

const LANE_H = 64;
const COLORS = ["#7cc4ff", "#ff9f6b", "#8fd694", "#d69be8", "#f2cf66", "#6fd6d6"];

interface Props {
  project: Project;
  selected: number | null;
  onSelect: (i: number) => void;
}

/** Every track on the output clock, with the overlaps shaded. Click an overlap to edit that transition. */
export function MixTimeline({ project, selected, onSelect }: Props) {
  const plan = project.plan!;
  const lanes = useMemo(() => layoutLanes(plan), [plan]);
  const scroller = useRef<HTMLDivElement>(null);
  const [px, setPx] = useState<number | null>(null);
  const total = plan.total_duration_s;

  useEffect(() => {
    if (px === null && scroller.current) {
      const w = scroller.current.clientWidth || 900;
      setPx(Math.min(20, Math.max(3, (w - 16) / Math.max(total, 1))));
    }
  }, [px, total]);

  const pxPerS = px ?? 6;
  const width = Math.max(300, Math.ceil(total * pxPerS));
  const step = rulerStep(pxPerS);
  const ticks: number[] = [];
  for (let t = 0; t <= total; t += step) ticks.push(t);

  return (
    <section className="panel mix-timeline" aria-label="Mix timeline">
      <div className="panel-head">
        <h2>The mix</h2>
        <span className="muted small">
          {plan.tracks.length} tracks · {fmtDuration(total)} · {plan.transitions.length} transitions
        </span>
        <label className="zoom small">
          Zoom
          <input
            type="range"
            min={2}
            max={40}
            step={0.5}
            value={pxPerS}
            onChange={(e) => setPx(Number(e.target.value))}
            aria-label="Timeline zoom"
          />
        </label>
      </div>
      <div className="timeline-scroll" ref={scroller}>
        <div className="timeline-inner" style={{ width, height: 24 + lanes.length * (LANE_H + 6) }}>
          <div className="ruler" aria-hidden="true">
            {ticks.map((t) => (
              <span key={t} style={{ left: t * pxPerS }}>
                {fmtTime(t, 0)}
              </span>
            ))}
          </div>
          {lanes.map((lane, i) => (
            <LaneRow key={lane.trackId} project={project} lane={lane} index={i} pxPerS={pxPerS} color={COLORS[i % COLORS.length]} />
          ))}
          {plan.transitions.map((tr) => (
            <button
              key={tr.index}
              className={"overlap" + (selected === tr.index ? " selected" : "") + (tr.warnings.length ? " has-warning" : "")}
              style={{ left: tr.start_s * pxPerS, width: Math.max(8, (tr.end_s - tr.start_s) * pxPerS) }}
              onClick={() => onSelect(tr.index)}
              aria-label={`Transition ${tr.index + 1}: ${plan.tracks[tr.index]?.name} into ${plan.tracks[tr.index + 1]?.name}, ${fmtTime(tr.start_s)} to ${fmtTime(tr.end_s)}`}
              aria-pressed={selected === tr.index}
            >
              <span>{tr.index + 1}</span>
            </button>
          ))}
        </div>
      </div>
    </section>
  );
}

function LaneRow({ project, lane, index, pxPerS, color }: { project: Project; lane: Lane; index: number; pxPerS: number; color: string }) {
  const track = project.tracks.find((t) => t.id === lane.trackId);
  const peaks = usePeaks(project.id, lane.trackId);
  const pt = project.plan!.tracks[index];
  const widthPx = Math.max(20, Math.round((lane.endS - lane.startS) * pxPerS));
  const phraseBars = project.settings.phrase_bars;
  const grid = useMemo(
    () => (pt ? { offset_s: pt.offset_s, bpm: pt.bpm, downbeat: pt.downbeat, phraseBars } : null),
    [pt, phraseBars],
  );
  return (
    <div className="lane" style={{ left: lane.startS * pxPerS, top: 24 + index * (LANE_H + 6), width: widthPx, height: LANE_H }}>
      <div className="lane-name" title={lane.name}>
        {index + 1}. {track?.name ?? lane.name}
      </div>
      <Waveform
        peaks={peaks}
        t0={lane.srcStart}
        t1={lane.srcEnd}
        width={widthPx}
        height={LANE_H}
        color={color}
        grid={grid}
        showGrid={widthPx / Math.max(1, lane.srcEnd - lane.srcStart) > 4}
        sections={pt?.sections ?? null}
        label={`Waveform of ${lane.name}`}
      />
    </div>
  );
}
