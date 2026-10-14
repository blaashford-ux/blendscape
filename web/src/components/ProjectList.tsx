import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { ProjectSummary } from "../api/types";

export function ProjectList() {
  const [projects, setProjects] = useState<ProjectSummary[] | null>(null);
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);

  const load = () =>
    api
      .listProjects()
      .then((p) => {
        setProjects(p);
        setError(null);
      })
      .catch((e: Error) => setError(e.message));
  useEffect(() => void load(), []);

  const create = async () => {
    try {
      const p = await api.createProject(name.trim() || "Untitled mix");
      window.location.hash = `#/p/${p.id}`;
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <main className="page">
      <h1>Your mixes</h1>
      <form
        className="new-project"
        onSubmit={(e) => {
          e.preventDefault();
          void create();
        }}
      >
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Name your mix (e.g. Friday set)" aria-label="New project name" />
        <button className="primary" type="submit">
          New mix
        </button>
      </form>
      {error && (
        <p className="error-text" role="alert">
          {error} <button onClick={() => void load()}>Retry</button>
        </p>
      )}
      {projects === null && !error && <p className="muted">Loading…</p>}
      {projects?.length === 0 && <p className="muted">Nothing here yet. Name a mix above and add some songs.</p>}
      <ul className="project-list">
        {projects?.map((p) => (
          <li key={p.id}>
            <a href={`#/p/${p.id}`} className="project-link">
              <strong>{p.name}</strong>
              <span className="muted small">
                {p.tracks} track{p.tracks === 1 ? "" : "s"} · edited {new Date(p.updated * 1000).toLocaleString()}
              </span>
            </a>
            <button
              className="icon-btn danger"
              aria-label={`Delete ${p.name}`}
              onClick={async () => {
                if (confirm(`Delete "${p.name}" and its rendered mixes? Your original audio files are not touched.`)) {
                  await api.deleteProject(p.id);
                  void load();
                }
              }}
            >
              ✕
            </button>
          </li>
        ))}
      </ul>
    </main>
  );
}
