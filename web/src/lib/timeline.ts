// Pure helpers: time formatting, beat-grid arithmetic, how tracks sit on the mix clock, and the EQ gain curves.
// Nothing here touches the DOM, so all of it is unit-tested.
import type { EqCurves, PlanDescription, PlanTrack, Section, Style, Transition } from "../api/types";

export const BEATS_PER_BAR = 4;

/** m:ss.s (or h:mm:ss) for positions in a song; "–" for missing values. */
export function fmtTime(s: number | null | undefined, decimals = 1): string {
  if (s === null || s === undefined || !Number.isFinite(s)) return "–";
  const sign = s < 0 ? "-" : "";
  s = Math.abs(s);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const secStr = sec.toFixed(decimals).padStart(decimals ? 3 + decimals : 2, "0");
  // rounding can produce 60.0
  if (secStr.startsWith("60")) return fmtTime(sign === "-" ? -(Math.floor(s) + 1) : Math.floor(s) + 1, decimals);
  return h > 0 ? `${sign}${h}:${String(m).padStart(2, "0")}:${secStr}` : `${sign}${m}:${secStr}`;
}

/** Whole seconds, for lengths: 4:52 */
export const fmtDuration = (s: number) => fmtTime(Math.round(s), 0);

export interface Grid {
  offset_s: number;
  bpm: number;
  downbeat: number;
}

export const periodS = (g: Grid) => 60 / g.bpm;
export const beatTime = (g: Grid, k: number) => g.offset_s + k * periodS(g);
export const nearestBeat = (g: Grid, t: number) => Math.round((t - g.offset_s) / periodS(g));
/** Bar number (0-based) a beat belongs to, counting from the first downbeat. Fractional for off-bar beats. */
export const barOf = (g: Grid, beat: number) => (beat - g.downbeat) / BEATS_PER_BAR;
export const isDownbeat = (g: Grid, beat: number) => (((beat - g.downbeat) % BEATS_PER_BAR) + BEATS_PER_BAR) % BEATS_PER_BAR === 0;

export interface GridLine {
  t: number;
  beat: number;
  kind: "beat" | "bar" | "phrase";
}

/** Grid lines inside [t0, t1] seconds: every beat, with bar lines and phrase lines marked. */
export function gridLines(g: Grid, t0: number, t1: number, phraseBars = 8): GridLine[] {
  const p = periodS(g);
  const first = Math.max(0, Math.ceil((t0 - g.offset_s) / p));
  const out: GridLine[] = [];
  for (let k = first; beatTime(g, k) <= t1; k++) {
    let kind: GridLine["kind"] = "beat";
    if (isDownbeat(g, k)) kind = Math.round(barOf(g, k)) % phraseBars === 0 ? "phrase" : "bar";
    out.push({ t: beatTime(g, k), beat: k, kind });
  }
  return out;
}

/** The beat nearest to time t, on the bar lines (step 4) or on any beat (step 1); never before the file starts. */
export function snapBeat(g: Grid, t: number, step: number): number {
  const k = (t - g.offset_s) / periodS(g);
  let beat = Math.round(g.downbeat + Math.round((k - g.downbeat) / step) * step);
  while (beat < 0) beat += step; // before the start of the file: the first line at or after it
  return beat;
}

/** An overlap length in beats for an end marker dragged to time t: whole bars (or beats), at least one bar. */
export function snapOverlap(g: Grid, startBeat: number, t: number, step: number): number {
  const k = (t - g.offset_s) / periodS(g);
  return Math.max(BEATS_PER_BAR, Math.round((k - startBeat) / step) * step);
}

/** Move a beat by whole bars, never below 0. */
export const nudgeBeat = (beat: number, bars: number) => Math.max(0, beat + bars * BEATS_PER_BAR);

export interface Lane {
  trackId: string;
  name: string;
  startS: number; // on the mix clock
  endS: number;
  srcStart: number; // the part of the file that is played, in file seconds
  srcEnd: number;
}

/** Where each track sits on the mix clock, and which stretch of its file is played there. */
export function layoutLanes(plan: PlanDescription): Lane[] {
  const n = plan.tracks.length;
  return plan.tracks.map((t, i) => {
    const entering: Transition | undefined = plan.transitions[i - 1];
    const leaving: Transition | undefined = plan.transitions[i];
    return {
      trackId: t.id,
      name: t.name,
      startS: entering ? entering.start_s : 0,
      endS: leaving ? leaving.end_s : plan.total_duration_s,
      srcStart: entering ? entering.in_time_s : 0,
      srcEnd: leaving ? beatTime(t, leaving.out_beat + leaving.overlap_beats) : i === n - 1 ? t.end_s : t.duration_s,
    };
  });
}

export const laneLength = (l: Lane) => Math.max(0, l.endS - l.startS);
/** Position in the file for a mix time, assuming the lane is played at an even rate (true outside the overlaps; the
 *  tempo glide inside them only shifts things by a few percent). */
export function mixToSrc(l: Lane, t: number): number {
  const len = laneLength(l);
  return len === 0 ? l.srcStart : l.srcStart + ((t - l.startS) / len) * (l.srcEnd - l.srcStart);
}
export function srcToMix(l: Lane, s: number): number {
  const span = l.srcEnd - l.srcStart;
  return span === 0 ? l.startS : l.startS + ((s - l.srcStart) / span) * laneLength(l);
}

export const sectionAt = (sections: Section[], bar: number): Section | undefined =>
  sections.find((s) => bar >= s.start_bar && bar < s.end_bar);

export const SECTION_COLORS: Record<Section["label"], string> = {
  intro: "#5b8def",
  body: "#4cc38a",
  breakdown: "#e0a43a",
  outro: "#c86bd8",
};

// ---------- EQ curves: must match src/blendscape/bands.py ----------
export interface BandGains {
  lo: number;
  mid: number;
  hi: number;
}

/** 0 before a, 1 after b, raised cosine between (same as the renderer). */
export function ramp(u: number, a: number, b: number): number {
  const t = Math.min(1, Math.max(0, (u - a) / Math.max(b - a, 1e-9)));
  return 0.5 - 0.5 * Math.cos(Math.PI * t);
}

/** Gains of the outgoing (a) and incoming (b) track at position `u` beats into an overlap of `n` beats. */
export function eqGains(eq: EqCurves, style: Style, n: number, u: number): { a: BandGains; b: BandGains } {
  if (style === "crossfade") {
    const t = Math.min(1, Math.max(0, u / n));
    const ca = Math.cos((t * Math.PI) / 2);
    const cb = Math.sin((t * Math.PI) / 2);
    return { a: { lo: ca, mid: ca, hi: ca }, b: { lo: cb, mid: cb, hi: cb } };
  }
  const c = eq.bass_swap_center * n;
  const h = eq.bass_swap_beats / 2;
  const swap = ramp(u, c - h, c + h);
  return {
    a: {
      lo: 1 - swap,
      mid: 1 - ramp(u, eq.mid_out[0] * n, eq.mid_out[1] * n),
      hi: 1 - ramp(u, eq.hi_out[0] * n, eq.hi_out[1] * n),
    },
    b: {
      lo: swap,
      mid: ramp(u, eq.mid_in[0] * n, eq.mid_in[1] * n),
      hi: ramp(u, eq.hi_in[0] * n, eq.hi_in[1] * n),
    },
  };
}

export function confidenceLabel(c: number): "sure" | "likely" | "unsure" {
  return c >= 0.8 ? "sure" : c >= 0.5 ? "likely" : "unsure";
}

export const LOW_CONFIDENCE = 0.5;

/** Whole bars (a multiple of 4 beats, at least one bar) closest to `seconds` at `bpm`: same rule as the planner. */
export function overlapBeatsForSeconds(seconds: number, bpm: number): number {
  return Math.max(1, Math.round((seconds * bpm) / 60 / BEATS_PER_BAR)) * BEATS_PER_BAR;
}

export function trackById(plan: PlanDescription, id: string): PlanTrack | undefined {
  return plan.tracks.find((t) => t.id === id);
}
