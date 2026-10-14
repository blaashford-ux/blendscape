import { fmtTime } from "../lib/timeline";
import { usePlayer } from "../state/usePlayer";

export function PlayerBar() {
  const p = usePlayer();
  if (!p.src) return null;
  const pct = (t: number) => (p.duration > 0 ? `${(t / p.duration) * 100}%` : "0%");
  return (
    <div className="player-bar" role="region" aria-label="Audio player">
      <button className="icon-btn" onClick={p.toggle} aria-label={p.playing ? "Pause" : "Play"}>
        {p.playing ? "❚❚" : "▶"}
      </button>
      <div className="player-title" title={p.title}>
        {p.title}
      </div>
      <div className="player-seek">
        {p.region && p.duration > 0 && (
          <div
            className="player-region"
            style={{ left: pct(p.region.start), width: `calc(${pct(p.region.end)} - ${pct(p.region.start)})` }}
            title="the overlap"
          />
        )}
        <input
          type="range"
          min={0}
          max={p.duration || 1}
          step={0.05}
          value={Math.min(p.time, p.duration || 1)}
          onChange={(e) => p.seek(Number(e.target.value))}
          aria-label="Seek"
        />
      </div>
      <div className="player-time" aria-live="off">
        {fmtTime(p.time)} / {fmtTime(p.duration)}
      </div>
      {p.error && <span className="error-text">{p.error}</span>}
      <button className="icon-btn" onClick={p.stop} aria-label="Close player">
        ✕
      </button>
    </div>
  );
}
