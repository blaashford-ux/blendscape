import { describe, expect, it } from "vitest";
import type { EqCurves, PlanDescription } from "../src/api/types";
import {
  barOf,
  beatTime,
  confidenceLabel,
  eqGains,
  fmtDuration,
  fmtTime,
  gridLines,
  isDownbeat,
  layoutLanes,
  mixToSrc,
  nearestBeat,
  nudgeBeat,
  overlapBeatsForSeconds,
  ramp,
  srcToMix,
} from "../src/lib/timeline";

describe("fmtTime", () => {
  it("formats minutes and seconds", () => {
    expect(fmtTime(0)).toBe("0:00.0");
    expect(fmtTime(93.27)).toBe("1:33.3");
    expect(fmtTime(59.96)).toBe("1:00.0"); // rounding must not produce 0:60.0
    expect(fmtTime(3725.5)).toBe("1:02:05.5");
    expect(fmtTime(75, 0)).toBe("1:15");
  });
  it("copes with missing and negative values", () => {
    expect(fmtTime(null)).toBe("–");
    expect(fmtTime(undefined)).toBe("–");
    expect(fmtTime(NaN)).toBe("–");
    expect(fmtTime(-5.25)).toBe("-0:05.3");
    expect(fmtDuration(292.06)).toBe("4:52");
  });
});

describe("beat grid", () => {
  const g = { offset_s: 0.5, bpm: 120, downbeat: 1 };
  it("converts between beats and time", () => {
    expect(beatTime(g, 0)).toBeCloseTo(0.5);
    expect(beatTime(g, 8)).toBeCloseTo(4.5);
    expect(nearestBeat(g, 4.6)).toBe(8);
    expect(nearestBeat(g, 4.9)).toBe(9);
  });
  it("knows where the bar lines are", () => {
    expect([0, 1, 2, 3, 4, 5, 9].map((k) => isDownbeat(g, k))).toEqual([false, true, false, false, false, true, true]);
    expect(barOf(g, 1)).toBe(0);
    expect(barOf(g, 33)).toBe(8);
  });
  it("lists grid lines with bars and phrases marked", () => {
    const lines = gridLines(g, 0, 20, 2); // 2-bar phrases: beats 1, 9, 17, ...
    expect(lines[0].beat).toBe(0);
    const kinds = Object.fromEntries(lines.map((l) => [l.beat, l.kind]));
    expect(kinds[1]).toBe("phrase");
    expect(kinds[5]).toBe("bar");
    expect(kinds[9]).toBe("phrase");
    expect(kinds[2]).toBe("beat");
    expect(lines.every((l) => l.t >= 0 && l.t <= 20)).toBe(true);
  });
  it("does not start before time zero", () => {
    expect(gridLines(g, -10, 2).every((l) => l.beat >= 0)).toBe(true);
  });
  it("nudges by whole bars and never below zero", () => {
    expect(nudgeBeat(100, 8)).toBe(132);
    expect(nudgeBeat(100, -1)).toBe(96);
    expect(nudgeBeat(2, -1)).toBe(0);
  });
});

describe("lanes on the mix clock", () => {
  const track = (id: string, bpm: number, offset: number, dur: number) => ({
    id,
    name: `${id}.wav`,
    bpm,
    downbeat: 0,
    confidence: 0.9,
    duration_s: dur,
    end_s: dur - 2,
    offset_s: offset,
    sections: [],
  });
  const tr = (i: number, from: string, to: string, start: number, end: number, outBeat: number, inTime: number) => ({
    index: i, from, to, style: "bass_swap" as const, overlap_beats: 16, overlap_s: end - start, out_beat: outBeat,
    in_beat: 0, out_time_s: start, in_time_s: inTime, start_s: start, end_s: end, unplayed_tail_s: 0, tempo_gap: 0,
    score: 0.8, note: "", warnings: [], alternatives: [],
    eq: { bass_swap_center: 0.5, bass_swap_beats: 4, mid_in: [0.15, 0.6], mid_out: [0.45, 0.85], hi_in: [0, 0.4], hi_out: [0.6, 1] } as EqCurves,
  });
  const plan: PlanDescription = {
    sample_rate: 44100,
    total_duration_s: 150,
    tracks: [track("a", 120, 0, 100), track("b", 120, 0.2, 90), track("c", 120, 0.1, 80)],
    transitions: [tr(0, "a", "b", 60, 68, 120, 0.2), tr(1, "b", "c", 110, 118, 100, 0.1)],
  };
  it("places each track between its entrance and its exit", () => {
    const [a, b, c] = layoutLanes(plan);
    expect([a.startS, a.endS]).toEqual([0, 68]);
    expect([b.startS, b.endS]).toEqual([60, 118]);
    expect([c.startS, c.endS]).toEqual([110, 150]);
    expect(a.srcStart).toBe(0);
    expect(b.srcStart).toBeCloseTo(0.2);
    expect(a.srcEnd).toBeCloseTo(0 + (120 + 16) * 0.5); // out beat + overlap, in the file
    expect(c.srcEnd).toBeCloseTo(78); // the last track plays to the end of its music
  });
  it("maps between mix time and file time", () => {
    const [a] = layoutLanes(plan);
    expect(mixToSrc(a, 0)).toBeCloseTo(a.srcStart);
    expect(mixToSrc(a, 68)).toBeCloseTo(a.srcEnd);
    expect(srcToMix(a, mixToSrc(a, 31.5))).toBeCloseTo(31.5);
  });
  it("handles a lane of zero length without dividing by zero", () => {
    const l = { trackId: "x", name: "x", startS: 5, endS: 5, srcStart: 2, srcEnd: 2 };
    expect(mixToSrc(l, 5)).toBe(2);
    expect(srcToMix(l, 2)).toBe(5);
  });
});

// Reference numbers produced by src/blendscape/bands.py (bass_swap_gains / crossfade_gains) for these settings.
describe("EQ curves match the renderer", () => {
  const eq: EqCurves = {
    bass_swap_center: 0.4, bass_swap_beats: 6, mid_in: [0.1, 0.5], mid_out: [0.5, 0.9], hi_in: [0, 0.3], hi_out: [0.7, 1],
  };
  const N = 16;
  const u = [0, 2, 4, 6.4, 8, 10, 12, 15, 16];
  const A = {
    lo: [1, 1, 0.975528, 0.5, 0.128428, 0, 0, 0, 0],
    mid: [1, 1, 1, 1, 1, 0.777785, 0.308658, 0, 0],
    hi: [1, 1, 1, 1, 1, 1, 0.933013, 0.103323, 0],
  };
  const B = {
    lo: [0, 0, 0.024472, 0.5, 0.871572, 1, 1, 1, 1],
    mid: [0, 0.009607, 0.308658, 0.853553, 1, 1, 1, 1, 1],
    hi: [0, 0.37059, 0.933013, 1, 1, 1, 1, 1, 1],
  };
  it("bass swap", () => {
    u.forEach((x, i) => {
      const g = eqGains(eq, "bass_swap", N, x);
      for (const band of ["lo", "mid", "hi"] as const) {
        expect(g.a[band]).toBeCloseTo(A[band][i], 5);
        expect(g.b[band]).toBeCloseTo(B[band][i], 5);
      }
    });
  });
  it("crossfade is equal power", () => {
    const xfA = [1, 0.980785, 0.92388, 0.809017, 0.707107, 0.55557, 0.382683, 0.098017, 0];
    u.forEach((x, i) => {
      const g = eqGains(eq, "crossfade", N, x);
      expect(g.a.lo).toBeCloseTo(xfA[i], 5);
      expect(g.a.lo ** 2 + g.b.lo ** 2).toBeCloseTo(1, 9);
      expect(g.a.mid).toBe(g.a.hi);
    });
  });
  it("ramp edges", () => {
    expect(ramp(-1, 0, 10)).toBe(0);
    expect(ramp(11, 0, 10)).toBe(1);
    expect(ramp(5, 0, 10)).toBeCloseTo(0.5);
  });
});

describe("small helpers", () => {
  it("rounds an overlap in seconds to whole bars like the planner", () => {
    expect(overlapBeatsForSeconds(8, 150)).toBe(20);
    expect(overlapBeatsForSeconds(10, 120)).toBe(20);
    expect(overlapBeatsForSeconds(5, 80)).toBe(8);
    expect(overlapBeatsForSeconds(0.5, 120)).toBe(4);
  });
  it("labels confidence", () => {
    expect(confidenceLabel(0.95)).toBe("sure");
    expect(confidenceLabel(0.6)).toBe("likely");
    expect(confidenceLabel(0.3)).toBe("unsure");
  });
});
