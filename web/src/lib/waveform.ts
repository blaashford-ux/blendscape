import type { Peaks } from "../api/types";

/**
 * Min and max sample value for each of `width` pixel columns covering [t0, t1] seconds of the file. Columns outside
 * the file are 0. The server sends a fixed number of peaks for the whole track, so zooming in shows them blockier but
 * never wrong.
 */
export function peakColumns(peaks: Peaks, t0: number, t1: number, width: number): { lo: Float32Array; hi: Float32Array } {
  const lo = new Float32Array(width);
  const hi = new Float32Array(width);
  const n = peaks.min.length;
  if (n === 0 || width <= 0 || t1 <= t0 || peaks.duration_s <= 0) return { lo, hi };
  const dt = (t1 - t0) / width;
  for (let x = 0; x < width; x++) {
    const a = t0 + x * dt;
    const b = a + dt;
    if (b <= 0 || a >= peaks.duration_s) continue;
    const i0 = Math.max(0, Math.floor((a / peaks.duration_s) * n));
    const i1 = Math.min(n, Math.max(i0 + 1, Math.ceil((b / peaks.duration_s) * n)));
    let mn = 0;
    let mx = 0;
    for (let i = i0; i < i1; i++) {
      if (peaks.min[i] < mn) mn = peaks.min[i];
      if (peaks.max[i] > mx) mx = peaks.max[i];
    }
    lo[x] = mn;
    hi[x] = mx;
  }
  return { lo, hi };
}

/** Pick tick spacing (seconds) for a time ruler so labels are roughly `minPx` apart. */
export function rulerStep(pxPerSecond: number, minPx = 70): number {
  const steps = [0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600];
  return steps.find((s) => s * pxPerSecond >= minPx) ?? 600;
}
