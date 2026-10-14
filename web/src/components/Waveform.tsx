import { useEffect, useMemo, useRef, useState, type MouseEvent } from "react";
import type { Peaks, Section } from "../api/types";
import { beatTime, gridLines, SECTION_COLORS, type Grid } from "../lib/timeline";
import { peakColumns } from "../lib/waveform";

export interface Marker {
  t: number; // file seconds
  color: string;
  label?: string;
}

export interface Region {
  t0: number;
  t1: number;
  color: string;
}

export interface WaveformProps {
  peaks: Peaks | null;
  /** The stretch of the file to draw, in seconds. */
  t0: number;
  t1: number;
  height?: number;
  color?: string;
  grid?: (Grid & { phraseBars: number }) | null;
  /** Draw the beat/bar/phrase lines (sections still need `grid` to know where bars are). Default true. */
  showGrid?: boolean;
  sections?: Section[] | null;
  markers?: Marker[];
  regions?: Region[];
  /** Fixed pixel width. Omit to fill the parent. */
  width?: number;
  onSeek?: (t: number) => void;
  label: string;
}

const css = (name: string, fallback: string) =>
  (typeof document !== "undefined" && getComputedStyle(document.documentElement).getPropertyValue(name).trim()) || fallback;

/** A waveform drawn on a canvas, with optional beat grid, song sections, markers and shaded regions on top. */
export function Waveform(props: WaveformProps) {
  const { peaks, t0, t1, height = 72, color = "#7cc4ff", grid, showGrid = true, sections, markers, regions, onSeek, label } = props;
  const box = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const [measured, setMeasured] = useState(props.width ?? 600);
  const width = props.width ?? measured;

  useEffect(() => {
    if (props.width !== undefined || !box.current) return;
    const el = box.current;
    const ro = new ResizeObserver(() => setMeasured(Math.max(50, Math.floor(el.clientWidth))));
    ro.observe(el);
    setMeasured(Math.max(50, Math.floor(el.clientWidth)));
    return () => ro.disconnect();
  }, [props.width]);

  const columns = useMemo(() => (peaks ? peakColumns(peaks, t0, t1, width) : null), [peaks, t0, t1, width]);

  useEffect(() => {
    const c = canvas.current;
    const ctx = c?.getContext?.("2d");
    if (!c || !ctx) return; // jsdom has no canvas
    const dpr = window.devicePixelRatio || 1;
    c.width = Math.floor(width * dpr);
    c.height = Math.floor(height * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);
    const span = t1 - t0 || 1;
    const x = (t: number) => ((t - t0) / span) * width;
    const mid = height / 2;
    const gridRgb = css("--grid-rgb", "255,255,255");

    for (const r of regions ?? []) {
      ctx.fillStyle = r.color;
      ctx.fillRect(x(r.t0), 0, x(r.t1) - x(r.t0), height);
    }
    if (sections && grid) {
      const sh = 5;
      for (const s of sections) {
        const a = x(beatTime(grid, grid.downbeat + 4 * s.start_bar));
        const b = x(beatTime(grid, grid.downbeat + 4 * s.end_bar));
        if (b < 0 || a > width) continue;
        ctx.fillStyle = SECTION_COLORS[s.label];
        ctx.globalAlpha = 0.85;
        ctx.fillRect(a, height - sh, Math.max(1, b - a - 1), sh);
        ctx.globalAlpha = 1;
      }
    }
    if (grid && showGrid) {
      const pxPerBeat = (width / span) * (60 / grid.bpm);
      const lines = gridLines(grid, t0, t1, grid.phraseBars);
      for (const g of lines) {
        if (pxPerBeat < 1.5 && g.kind !== "phrase") continue;
        if (pxPerBeat < 5 && g.kind === "beat") continue;
        const alpha = g.kind === "phrase" ? 0.6 : g.kind === "bar" ? 0.32 : 0.14;
        ctx.strokeStyle = `rgba(${gridRgb}, ${alpha})`;
        ctx.lineWidth = 1;
        ctx.beginPath();
        const px = Math.round(x(g.t)) + 0.5;
        ctx.moveTo(px, 0);
        ctx.lineTo(px, height);
        ctx.stroke();
      }
    }
    if (columns) {
      ctx.fillStyle = color;
      const amp = (height / 2) * 0.88;
      for (let i = 0; i < width; i++) {
        const top = mid - columns.hi[i] * amp;
        const bottom = mid - columns.lo[i] * amp;
        ctx.fillRect(i, top, 1, Math.max(1, bottom - top));
      }
    } else {
      ctx.fillStyle = css("--muted", "#8892a0");
      ctx.font = "12px system-ui, sans-serif";
      ctx.fillText("loading waveform…", 8, mid + 4);
    }
    for (const m of markers ?? []) {
      const px = Math.round(x(m.t)) + 0.5;
      if (px < -1 || px > width + 1) continue;
      ctx.strokeStyle = m.color;
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(px, 0);
      ctx.lineTo(px, height);
      ctx.stroke();
    }
  }, [columns, width, height, t0, t1, color, grid, showGrid, sections, markers, regions]);

  const click = (e: MouseEvent) => {
    if (!onSeek || !box.current) return;
    const r = box.current.getBoundingClientRect();
    onSeek(t0 + ((e.clientX - r.left) / r.width) * (t1 - t0));
  };

  const labels = (markers ?? []).filter((m) => m.label && m.t >= t0 && m.t <= t1);
  return (
    <div
      ref={box}
      className={"waveform" + (onSeek ? " clickable" : "")}
      style={{ height, width: props.width }}
      onClick={click}
      data-testid="waveform"
    >
      <canvas ref={canvas} role="img" aria-label={label} style={{ width: "100%", height }} />
      {labels.map((m) => (
        <span
          key={`${m.label}-${m.t}`}
          className="marker-label"
          style={{ left: `${((m.t - t0) / (t1 - t0)) * 100}%`, borderColor: m.color, color: m.color }}
        >
          {m.label}
        </span>
      ))}
    </div>
  );
}
