import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { AppConfig } from "../api/types";

/** Settings that belong to this computer, not to a mix: where the Rubber Band program is, and a folder for copies of finished mixes. */
export function MachineSettings({ onChanged }: { onChanged?: () => void }) {
  const [cfg, setCfg] = useState<AppConfig | null>(null);
  const [rb, setRb] = useState("");
  const [dir, setDir] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);

  const adopt = (c: AppConfig) => {
    setCfg(c);
    setRb(c.rubberband ?? "");
    setDir(c.export_dir ?? "");
  };
  useEffect(() => {
    api.config().then(adopt).catch((e: Error) => setError(e.message));
  }, []);

  const save = async (what: "rubberband" | "export_dir", value: string) => {
    setError(null);
    setSaved(null);
    try {
      adopt(await api.setConfig({ [what]: value.trim() || null }));
      setSaved(what === "rubberband" ? "Rubber Band location saved." : "Export folder saved.");
      onChanged?.();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  if (!cfg) return error ? <p className="error-box" role="alert">{error}</p> : null;
  return (
    <section className="panel machine-settings" aria-label="This computer">
      <h2>This computer</h2>
      <div className="machine-row">
        <label className="field wide">
          <span>Rubber Band program</span>
          <input
            type="text"
            value={rb}
            placeholder="e.g. C:\tools\rubberband\rubberband.exe"
            onChange={(e) => setRb(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && void save("rubberband", rb)}
            aria-label="Rubber Band program"
          />
        </label>
        <button onClick={() => void save("rubberband", rb)} disabled={rb.trim() === (cfg.rubberband ?? "")}>
          Save
        </button>
      </div>
      <p className={"small " + (cfg.rubberband_in_use ? "muted" : "warn")}>
        {cfg.rubberband_in_use
          ? `In use: ${cfg.rubberband_in_use}${cfg.rubberband_version ? ` (${cfg.rubberband_version})` : ""}`
          : "Not found yet: previews and renders need it. Type the full path to rubberband.exe (or its folder) and save; no restart is needed."}
      </p>
      <div className="machine-row">
        <label className="field wide">
          <span>Also save a copy of each rendered mix in this folder</span>
          <input
            type="text"
            value={dir}
            placeholder="e.g. C:\Users\you\Music\Mixes (blank: keep them only in the project)"
            onChange={(e) => setDir(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && void save("export_dir", dir)}
            aria-label="Export folder"
          />
        </label>
        <button onClick={() => void save("export_dir", dir)} disabled={dir.trim() === (cfg.export_dir ?? "")}>
          Save
        </button>
      </div>
      {error && (
        <p className="error-box" role="alert">
          {error}
        </p>
      )}
      {saved && (
        <p className="small muted" role="status">
          {saved}
        </p>
      )}
    </section>
  );
}
