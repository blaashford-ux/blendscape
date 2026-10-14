import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { Project, SettingsPatch, Style } from "../api/types";

interface Props {
  project: Project;
  onProject: (p: Project) => void;
  disabled?: boolean;
}

/** A number box that only sends its value when you leave it or press Enter, not on every keystroke. */
function NumberField({ label, value, min, max, step, disabled, onCommit }: { label: string; value: number; min: number; max: number; step: number; disabled?: boolean; onCommit: (v: number) => void }) {
  const [text, setText] = useState(String(value));
  useEffect(() => setText(String(value)), [value]);
  const commit = () => {
    const n = Number(text);
    if (text.trim() === "" || !Number.isFinite(n)) return setText(String(value));
    const clamped = Math.min(max, Math.max(min, n));
    setText(String(clamped));
    if (clamped !== value) onCommit(clamped);
  };
  return (
    <label className="field">
      <span>{label}</span>
      <input
        type="number"
        min={min}
        max={max}
        step={step}
        value={text}
        disabled={disabled}
        onChange={(e) => setText(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => e.key === "Enter" && commit()}
      />
    </label>
  );
}

export function SettingsPanel({ project, onProject, disabled }: Props) {
  const s = project.settings;
  const [error, setError] = useState<string | null>(null);
  const change = async (patch: SettingsPatch) => {
    setError(null);
    try {
      onProject(await api.patchSettings(project.id, patch));
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const bySeconds = s.overlap_seconds !== null;

  return (
    <section className="panel settings" aria-label="Settings">
      <h2>Settings</h2>
      <p className="hint small">Changes apply the next time you plan (the plan is marked out of date). Edits you made to a single transition are kept.</p>
      <div className="settings-grid">
        <fieldset>
          <legend>Overlap between songs</legend>
          <label className="radio">
            <input type="radio" name="overlap-mode" checked={!bySeconds} disabled={disabled} onChange={() => change({ clear_overlap_seconds: true })} />
            by beats
          </label>
          <label className="radio">
            <input type="radio" name="overlap-mode" checked={bySeconds} disabled={disabled} onChange={() => change({ overlap_seconds: 8 })} />
            by seconds
          </label>
          {bySeconds ? (
            <NumberField label="Seconds" value={s.overlap_seconds ?? 8} min={1} max={60} step={0.5} disabled={disabled} onCommit={(v) => change({ overlap_seconds: v })} />
          ) : (
            <NumberField label="Beats (32 = 8 bars)" value={s.overlap_beats} min={2} max={256} step={2} disabled={disabled} onCommit={(v) => change({ overlap_beats: v })} />
          )}
          <p className="hint small">Songs with vocals usually sound better with 5–10 seconds.</p>
        </fieldset>

        <fieldset>
          <legend>Choosing where to mix</legend>
          <label className="field">
            <span>Mix points</span>
            <select value={s.mix_points} disabled={disabled} onChange={(e) => change({ mix_points: e.target.value as "smart" | "latest" })}>
              <option value="smart">Smart (use the song structure)</option>
              <option value="latest">As late as possible</option>
            </select>
          </label>
          <label className="field">
            <span>Phrase length</span>
            <select value={s.phrase_bars} disabled={disabled} onChange={(e) => change({ phrase_bars: Number(e.target.value) })}>
              {[1, 2, 4, 8, 16].map((n) => (
                <option key={n} value={n}>
                  {n} bar{n > 1 ? "s" : ""}
                </option>
              ))}
            </select>
          </label>
          <NumberField label="Most of a song left unplayed (s, 0 = no limit)" value={s.max_cut_seconds} min={0} max={120} step={1} disabled={disabled} onCommit={(v) => change({ max_cut_seconds: v })} />
          <label className="field">
            <span>Default blend style</span>
            <select value={s.style} disabled={disabled} onChange={(e) => change({ style: e.target.value as Style })}>
              <option value="bass_swap">Bass swap (DJ-style)</option>
              <option value="crossfade">Simple crossfade</option>
            </select>
          </label>
        </fieldset>

        <fieldset>
          <legend>Output</legend>
          <label className="field">
            <span>Bit depth</span>
            <select value={s.bit_depth} disabled={disabled} onChange={(e) => change({ bit_depth: Number(e.target.value) as 16 | 24 | 32 })}>
              <option value={16}>16-bit</option>
              <option value={24}>24-bit</option>
              <option value={32}>32-bit float</option>
            </select>
          </label>
          <label className="field">
            <span>Sample rate</span>
            <select value={s.sample_rate} disabled={disabled} onChange={(e) => change({ sample_rate: Number(e.target.value) })}>
              {[44100, 48000, 88200, 96000].map((r) => (
                <option key={r} value={r}>
                  {r / 1000} kHz
                </option>
              ))}
            </select>
          </label>
          <NumberField label="Peak ceiling (dB)" value={s.ceiling_db} min={-24} max={0} step={0.5} disabled={disabled} onCommit={(v) => change({ ceiling_db: v })} />
          <label className="radio">
            <input type="checkbox" checked={s.limiter.enabled} disabled={disabled} onChange={(e) => change({ limiter: { enabled: e.target.checked } })} />
            Limiter on (keeps loud overlaps from clipping)
          </label>
        </fieldset>
      </div>
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
