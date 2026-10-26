import { useRef, useState, type DragEvent } from "react";
import { api, uploadTrack } from "../api/client";
import type { Project, TrackView } from "../api/types";
import { confidenceLabel, fmtDuration, LOW_CONFIDENCE } from "../lib/timeline";
import { usePlayer } from "../state/usePlayer";

interface Props {
  project: Project;
  /** Called with a fresh project returned by an API call. */
  onProject: (p: Project) => void;
  /** Called after tracks were added or removed so the parent can re-fetch and (re)analyse. */
  onTracksChanged: (added: boolean) => void;
  disabled?: boolean;
}

interface Upload {
  key: number;
  name: string;
  fraction: number;
  error?: string;
}

export function TrackList({ project, onProject, onTracksChanged, disabled }: Props) {
  const player = usePlayer();
  const [dragId, setDragId] = useState<string | null>(null);
  const [overId, setOverId] = useState<string | null>(null);
  const [uploads, setUploads] = useState<Upload[]>([]);
  const [fileDrag, setFileDrag] = useState(false);
  const [pathOpen, setPathOpen] = useState(false);
  const [path, setPath] = useState("");
  const [error, setError] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const counter = useRef(0);

  const guard = async (fn: () => Promise<void>) => {
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const move = (id: string, to: number) =>
    guard(async () => {
      const order = project.tracks.map((t) => t.id).filter((x) => x !== id);
      order.splice(Math.max(0, Math.min(order.length, to)), 0, id);
      if (order.join() === project.tracks.map((t) => t.id).join()) return;
      onProject(await api.reorder(project.id, order));
    });

  const shuffle = () => {
    const edited = project.plan?.transitions.filter((t) => t.edited).length ?? 0;
    if (edited > 0 && !window.confirm(`Shuffling plans the transitions again, so your edits to ${edited === 1 ? "a transition" : `${edited} transitions`} will be lost unless the same two songs end up next to each other. Shuffle anyway?`)) return;
    void guard(async () => onProject(await api.shuffle(project.id)));
  };

  const remove = (t: TrackView) =>
    guard(async () => {
      await api.deleteTrack(project.id, t.id);
      onTracksChanged(false);
    });

  const nudgeBarLine = (t: TrackView, delta: number) =>
    guard(async () => {
      const cur = t.analysis?.downbeat ?? 0;
      onProject(await api.setDownbeat(project.id, t.id, (cur + delta + 4) % 4));
    });

  const resetBarLine = (t: TrackView) => guard(async () => onProject(await api.setDownbeat(project.id, t.id, null)));

  const addFiles = async (files: File[]) => {
    const audio = files.filter((f) => f.type.startsWith("audio/") || /\.(wav|flac|ogg|aiff?|mp3)$/i.test(f.name));
    if (audio.length === 0) {
      setError("Drop audio files (WAV works best).");
      return;
    }
    setError(null);
    let added = 0;
    for (const f of audio) {
      const key = ++counter.current;
      setUploads((u) => [...u, { key, name: f.name, fraction: 0 }]);
      try {
        await uploadTrack(project.id, f, (fraction) =>
          setUploads((u) => u.map((x) => (x.key === key ? { ...x, fraction } : x))),
        );
        added++;
        setUploads((u) => u.filter((x) => x.key !== key));
      } catch (e) {
        setUploads((u) => u.map((x) => (x.key === key ? { ...x, error: (e as Error).message } : x)));
      }
    }
    if (added) onTracksChanged(true);
  };

  const addPath = () =>
    guard(async () => {
      const p = path.trim().replace(/^"(.*)"$/, "$1");
      if (!p) return;
      await api.addTrackByPath(project.id, p);
      setPath("");
      onTracksChanged(true);
    });

  const onDragOverList = (e: DragEvent) => {
    if (e.dataTransfer.types.includes("Files")) {
      e.preventDefault();
      setFileDrag(true);
    }
  };
  const onDropList = (e: DragEvent) => {
    setFileDrag(false);
    if (e.dataTransfer.files.length) {
      e.preventDefault();
      void addFiles(Array.from(e.dataTransfer.files));
    }
  };

  return (
    <section
      className={"panel track-list" + (fileDrag ? " file-drag" : "")}
      onDragOver={onDragOverList}
      onDragLeave={() => setFileDrag(false)}
      onDrop={onDropList}
      aria-label="Tracks"
    >
      <div className="track-list-head">
        <h2>Tracks</h2>
        <button
          disabled={disabled || project.tracks.length < 2}
          onClick={shuffle}
          title="Put the songs in a random order and plan the transitions again"
        >
          🔀 Shuffle
        </button>
      </div>
      {project.tracks.length === 0 && (
        <p className="hint">Add the songs you want to blend, in the order you want to hear them. Drop audio files here.</p>
      )}
      <ol className="tracks">
        {project.tracks.map((t, i) => (
          <li
            key={t.id}
            className={"track" + (dragId === t.id ? " dragging" : "") + (overId === t.id && dragId !== t.id ? " over" : "")}
            draggable={!disabled}
            onDragStart={(e) => {
              setDragId(t.id);
              e.dataTransfer.effectAllowed = "move";
              e.dataTransfer.setData("text/plain", t.id);
            }}
            onDragOver={(e) => {
              if (dragId) {
                e.preventDefault();
                setOverId(t.id);
              }
            }}
            onDrop={(e) => {
              if (!dragId) return;
              e.preventDefault();
              e.stopPropagation();
              const id = dragId;
              setDragId(null);
              setOverId(null);
              void move(id, i);
            }}
            onDragEnd={() => {
              setDragId(null);
              setOverId(null);
            }}
          >
            <div className="track-head">
              <span className="grip" aria-hidden="true" title="Drag to reorder">
                ⋮⋮
              </span>
              <span className="track-num">{i + 1}</span>
              <span className="track-name" title={t.name}>
                {t.name}
              </span>
              <span className="track-actions">
                <button className="icon-btn" aria-label={`Move ${t.name} up`} disabled={disabled || i === 0} onClick={() => move(t.id, i - 1)}>
                  ▲
                </button>
                <button
                  className="icon-btn"
                  aria-label={`Move ${t.name} down`}
                  disabled={disabled || i === project.tracks.length - 1}
                  onClick={() => move(t.id, i + 1)}
                >
                  ▼
                </button>
                <button className="icon-btn danger" aria-label={`Remove ${t.name}`} disabled={disabled} onClick={() => remove(t)}>
                  ✕
                </button>
              </span>
            </div>
            {t.missing && <div className="error-text">The file is no longer at its original location.</div>}
            {t.analysis ? (
              <TrackFacts project={project} t={t} disabled={disabled} onNudge={nudgeBarLine} onReset={resetBarLine} player={player} />
            ) : (
              <div className="muted small">
                {fmtDuration(t.duration_s)} · {t.analysed ? "" : "not analysed yet"}
              </div>
            )}
          </li>
        ))}
      </ol>

      {uploads.map((u) => (
        <div key={u.key} className="upload">
          <span className="track-name">{u.name}</span>
          {u.error ? (
            <>
              <span className="error-text">{u.error}</span>
              <button className="icon-btn" aria-label="Dismiss" onClick={() => setUploads((x) => x.filter((y) => y.key !== u.key))}>
                ✕
              </button>
            </>
          ) : (
            <progress max={1} value={u.fraction} aria-label={`Uploading ${u.name}`} />
          )}
        </div>
      ))}

      <div className="add-row">
        <button className="primary" disabled={disabled} onClick={() => input.current?.click()}>
          Add audio files…
        </button>
        <input
          ref={input}
          type="file"
          accept="audio/*,.wav,.flac,.ogg,.aif,.aiff"
          multiple
          hidden
          data-testid="file-input"
          onChange={(e) => {
            const files = Array.from(e.target.files ?? []);
            e.target.value = "";
            void addFiles(files);
          }}
        />
        <button className="link" onClick={() => setPathOpen((o) => !o)} aria-expanded={pathOpen}>
          or add by file path
        </button>
      </div>
      {pathOpen && (
        <form
          className="path-form"
          onSubmit={(e) => {
            e.preventDefault();
            void addPath();
          }}
        >
          <input
            value={path}
            onChange={(e) => setPath(e.target.value)}
            placeholder="C:\Music\song.wav  or  /home/me/song.wav"
            aria-label="File path"
          />
          <button type="submit" disabled={!path.trim() || disabled}>
            Add
          </button>
          <p className="hint">The file is used where it is (not copied), which is quicker for big files.</p>
        </form>
      )}
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}

function TrackFacts({
  project,
  t,
  disabled,
  onNudge,
  onReset,
  player,
}: {
  project: Project;
  t: TrackView;
  disabled?: boolean;
  onNudge: (t: TrackView, d: number) => void;
  onReset: (t: TrackView) => void;
  player: ReturnType<typeof usePlayer>;
}) {
  const a = t.analysis!;
  const conf = a.downbeat_confidence;
  const unsure = !a.downbeat_manual && conf < LOW_CONFIDENCE;
  return (
    <div className="track-facts">
      <div className="small">
        {a.bpm.toFixed(1)} BPM · {fmtDuration(t.duration_s)}
      </div>
      <div className="barline small">
        <span>Bar line:</span>
        {a.downbeat_manual ? (
          <span className="chip manual">set by you</span>
        ) : (
          <span className={`chip conf-${confidenceLabel(conf)}`} title="How sure the detector is which beat starts a bar">
            {confidenceLabel(conf)} {Math.round(conf * 100)}%
          </span>
        )}
        <button className="icon-btn" aria-label={`Move the bar line of ${t.name} one beat earlier`} disabled={disabled} onClick={() => onNudge(t, -1)}>
          ◀
        </button>
        <button className="icon-btn" aria-label={`Move the bar line of ${t.name} one beat later`} disabled={disabled} onClick={() => onNudge(t, +1)}>
          ▶
        </button>
        {a.downbeat_manual && (
          <button className="link" disabled={disabled} onClick={() => onReset(t)}>
            auto
          </button>
        )}
      </div>
      {unsure && <div className="warn small">Unsure where the bars start: listen to the click and shift it if needed.</div>}
      <div className="track-buttons">
        <button onClick={() => player.play(api.clickUrl(project.id, t.id), `${t.name}: bar-line check (high click = bar start)`)}>
          Listen to bar line
        </button>
        <button onClick={() => player.play(api.audioUrl(project.id, t.id), t.name)}>Play</button>
      </div>
    </div>
  );
}
