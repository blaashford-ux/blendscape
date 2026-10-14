import { describe, expect, it } from "vitest";
import { peakColumns, rulerStep } from "../src/lib/waveform";

const peaks = (min: number[], max: number[], duration = 10) => ({ points: min.length, min, max, duration_s: duration, sample_rate: 44100 });

describe("peakColumns", () => {
  it("maps one peak per column when the zoom matches", () => {
    const p = peaks([-1, -2, -3, -4], [1, 2, 3, 4], 4);
    const { lo, hi } = peakColumns(p, 0, 4, 4);
    expect([...lo]).toEqual([-1, -2, -3, -4]);
    expect([...hi]).toEqual([1, 2, 3, 4]);
  });
  it("takes the extremes when several peaks fall in one column", () => {
    const p = peaks([-1, -5, -2, -3], [1, 2, 6, 3], 4);
    const { lo, hi } = peakColumns(p, 0, 4, 2);
    expect([...lo]).toEqual([-5, -3]);
    expect([...hi]).toEqual([2, 6]);
  });
  it("repeats a peak across columns when zoomed in", () => {
    const p = peaks([-1, -9], [1, 9], 2);
    const { hi } = peakColumns(p, 0, 2, 8);
    expect([...hi]).toEqual([1, 1, 1, 1, 9, 9, 9, 9]);
  });
  it("is silent outside the file", () => {
    const p = peaks([-1, -1], [1, 1], 2);
    const { lo, hi } = peakColumns(p, -2, 4, 6);
    expect([...hi]).toEqual([0, 0, 1, 1, 0, 0]);
    expect([...lo]).toEqual([0, 0, -1, -1, 0, 0]);
  });
  it("handles empty or degenerate input", () => {
    expect([...peakColumns(peaks([], [], 5), 0, 5, 3).hi]).toEqual([0, 0, 0]);
    expect(peakColumns(peaks([-1], [1], 1), 3, 3, 4).hi.length).toBe(4);
    expect(peakColumns(peaks([-1], [1], 1), 0, 1, 0).hi.length).toBe(0);
  });
});

describe("rulerStep", () => {
  it("grows as you zoom out", () => {
    expect(rulerStep(40)).toBe(2);
    expect(rulerStep(10)).toBe(10);
    expect(rulerStep(2)).toBe(60);
    expect(rulerStep(0.01)).toBe(600);
  });
});
