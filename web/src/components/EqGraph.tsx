import type { EqCurves, Style } from "../api/types";
import { eqGains } from "../lib/timeline";

const W = 360;
const H = 120;
const PAD = 6;

interface Props {
  eq: EqCurves;
  style: Style;
  beats: number;
}

/** The six gain curves of a transition: solid = outgoing track, dashed = incoming; low, mid and high bands. */
export function EqGraph({ eq, style, beats }: Props) {
  const steps = 80;
  const bands = [
    { key: "lo" as const, color: "#ff7a7a", name: "bass" },
    { key: "mid" as const, color: "#8fd694", name: "mids" },
    { key: "hi" as const, color: "#7cc4ff", name: "highs" },
  ];
  const xy = (i: number, g: number) => `${PAD + (i / steps) * (W - 2 * PAD)},${PAD + (1 - g) * (H - 2 * PAD)}`;
  const path = (side: "a" | "b", band: "lo" | "mid" | "hi") =>
    Array.from({ length: steps + 1 }, (_, i) => xy(i, eqGains(eq, style, beats, (i / steps) * beats)[side][band])).join(" ");
  return (
    <figure className="eq-graph">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="How the bass, mids and highs of each track fade over the overlap">
        <rect x={PAD} y={PAD} width={W - 2 * PAD} height={H - 2 * PAD} fill="none" stroke="rgba(255,255,255,.15)" />
        <line x1={W / 2} x2={W / 2} y1={PAD} y2={H - PAD} stroke="rgba(255,255,255,.08)" />
        {bands.map((b) => (
          <g key={b.key}>
            <polyline points={path("a", b.key)} fill="none" stroke={b.color} strokeWidth={2} />
            <polyline points={path("b", b.key)} fill="none" stroke={b.color} strokeWidth={2} strokeDasharray="5 4" opacity={0.8} />
          </g>
        ))}
      </svg>
      <figcaption className="small muted">
        <span style={{ color: bands[0].color }}>bass</span> · <span style={{ color: bands[1].color }}>mids</span> ·{" "}
        <span style={{ color: bands[2].color }}>highs</span> — solid: leaving track, dashed: arriving track
      </figcaption>
    </figure>
  );
}
