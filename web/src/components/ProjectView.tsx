import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type { PreviewResult, Project, RenderResult } from "../api/types";
import { fmtDuration } from "../lib/timeline";
import { useJobRunner } from "../state/useJobRunner";
import { usePlayer } from "../state/usePlayer";
import { useProject } from "../state/useProject";
import { JobBar } from "./JobBar";
import { MixTimeline } from "./MixTimeline";
import { MachineSettings } from "./MachineSettings";
import { SettingsPanel } from "./SettingsPanel";
import { TrackList } from "./TrackList";
import { TransitionEditor } from "./TransitionEditor";

export function ProjectView({ id }: { id: string }) {
  const { project, error, refresh, apply } = useProject(id);
  const work = useJobRunner();
  const previewJob = useJobRunner();
  const player = usePlayer();
  const [selected, setSelected] = useState<number | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [stretch, setStretch] = useState<string | null | undefined>(undefined);
  const [draftName, setDraftName] = useState<string | null>(null); // only while you are typing

  const checkStretch = useCallback(() => {
    api.health().then((h) => setStretch(h.stretch_backend)).catch(() => setStretch(undefined));
  }, []);
  useEffect(checkStretch, [checkStretch]);

  const nTransitions = project?.plan?.transitions.length ?? 0;
  useEffect(() => {
    if (selected !== null && selected >= nTransitions) setSelected(nTransitions ? 0 : null);
    if (selected === null && nTransitions) setSelected(0);
  }, [nTransitions, selected]);

  const analyse = useCallback(
    async (force = false) => {
      setNotice(null);
      const done = await work.start("Analysing the tracks", () => api.analyze(id, { replan: true, force }));
      await refresh();
      return done;
    },
    [id, work, refresh],
  );

  const replan = async () => {
    setNotice(null);
    try {
      apply(await api.plan(id));
    } catch (e) {
      setNotice((e as Error).message);
    }
  };

  const render = async () => {
    setNotice(null);
    setInfo(null);
    const done = await work.start("Rendering the mix", () => api.render(id));
    await refresh();
    if (done?.result) {
      const r = done.result as unknown as RenderResult;
      if (r.clipped) setNotice("The mix clipped: turn the limiter on or lower the ceiling.");
      else if (r.copy_error) setNotice(r.copy_error);
      if (r.copied_to) setInfo(`Saved a copy to ${r.copied_to}`);
    }
  };

  const startPreview = async () => {
    if (selected === null || !project?.plan) return;
    const tr = project.plan.transitions[selected];
    const title = `Preview: ${project.plan.tracks[selected].name} → ${project.plan.tracks[selected + 1].name}`;
    const done = await previewJob.start("Rendering a preview", () => api.preview(id, selected));
    if (done?.result) {
      const r = done.result as unknown as PreviewResult;
      player.play(r.url + `?t=${Date.now()}`, title, { region: { start: r.overlap_start_s, end: r.overlap_end_s } });
    }
    void tr;
  };

  const tracksChanged = async (added: boolean) => {
    const p = await refresh();
    if (added && p && p.tracks.some((t) => !t.analysed) && !work.running) void analyse();
    else if (p) setNotice(null);
  };

  if (error && !project) {
    return (
      <main className="page">
        <p className="error-text" role="alert">
          Could not open this project: {error}
        </p>
        <a href="#/">Back to projects</a>
      </main>
    );
  }
  if (!project) return <main className="page muted">Loading…</main>;

  const busy = work.running;
  const analysed = project.tracks.length > 0 && project.tracks.every((t) => t.analysed);
  const needsAnalysis = project.tracks.some((t) => !t.analysed);
  const plan = project.plan;

  return (
    <main className="project">
      <header className="project-head">
        <input
          className="project-name"
          value={draftName ?? project.name}
          aria-label="Project name"
          onChange={(e) => setDraftName(e.target.value)}
          onBlur={() => {
            const n = (draftName ?? project.name).trim();
            setDraftName(null);
            if (n && n !== project.name) void api.renameProject(id, n).then(apply);
          }}
          onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
        />
        <div className="head-actions">
          <button onClick={() => setSettingsOpen((o) => !o)} aria-expanded={settingsOpen}>
            Settings
          </button>
          <button className={needsAnalysis ? "primary" : ""} disabled={busy || project.tracks.length === 0} onClick={() => analyse(!needsAnalysis)}>
            {needsAnalysis ? "Analyse & plan" : "Re-analyse"}
          </button>
          <button className="primary" disabled={busy || !plan || project.plan_stale || stretch === null} onClick={render}>
            Render the mix
          </button>
        </div>
      </header>

      <JobBar run={work.run} onCancel={work.cancel} onDismiss={work.dismiss} />
      <JobBar run={previewJob.run} onCancel={previewJob.cancel} onDismiss={previewJob.dismiss} />

      {stretch === null && (
        <div className="banner warn-banner" role="alert">
          <span>The Rubber Band program wasn't found, so previews and renders are unavailable.</span>
          <button className="primary" onClick={() => setSettingsOpen(true)}>
            Tell me where it is
          </button>
        </div>
      )}
      {project.plan_stale && (
        <div className="banner" role="status">
          <span>Tracks or settings changed since the last plan.</span>
          <button className="primary" disabled={busy || needsAnalysis} onClick={replan}>
            Plan again
          </button>
        </div>
      )}
      {project.plan_warnings.map((w) => (
        <div key={w} className="banner warn-banner" role="status">
          {w}
        </div>
      ))}
      {info && (
        <div className="banner" role="status">
          {info}
        </div>
      )}
      {notice && (
        <div className="banner warn-banner" role="alert">
          {notice}
        </div>
      )}

      {settingsOpen && (
        <>
          <SettingsPanel project={project} onProject={apply} disabled={busy} />
          <MachineSettings onChanged={checkStretch} />
        </>
      )}

      <div className="workspace">
        <aside className="sidebar">
          <TrackList project={project} onProject={apply} onTracksChanged={tracksChanged} disabled={busy} />
        </aside>
        <div className="main">
          {project.tracks.length === 0 && (
            <div className="empty panel">
              <h2>Start with two or more songs</h2>
              <p>
                Add them on the left in the order you want them played. Blendscape finds each song's tempo and bars, picks where one should
                hand over to the next, and blends them like a DJ would: beat-matched, with the bass swapping at the right moment.
              </p>
            </div>
          )}
          {project.tracks.length === 1 && analysed && <div className="empty panel">Add at least one more song to make a blend.</div>}
          {needsAnalysis && project.tracks.length > 0 && !busy && (
            <div className="empty panel">
              <p>Some tracks haven't been analysed yet.</p>
              <button className="primary" onClick={() => analyse()}>
                Analyse & plan
              </button>
            </div>
          )}
          {plan && plan.transitions.length > 0 && (
            <>
              <MixTimeline project={project} selected={selected} onSelect={setSelected} />
              {selected !== null && plan.transitions[selected] && (
                <TransitionEditor
                  key={`${plan.tracks[selected].id}-${plan.tracks[selected + 1].id}`}
                  project={project}
                  index={selected}
                  onProject={apply}
                  preview={{ run: previewJob.run, onStart: startPreview, onCancel: previewJob.cancel }}
                />
              )}
            </>
          )}
          <Mixes project={project} />
        </div>
      </div>
    </main>
  );
}

function Mixes({ project }: { project: Project }) {
  const player = usePlayer();
  if (project.mixes.length === 0) return null;
  return (
    <section className="panel mixes" aria-label="Rendered mixes">
      <h2>Rendered mixes</h2>
      <ul>
        {project.mixes.map((m) => {
          const url = `/api/projects/${project.id}/mixes/${encodeURIComponent(m.name)}`;
          return (
            <li key={m.name}>
              <span className="track-name">{m.name}</span>
              <span className="muted small">{(m.size / 1e6).toFixed(0)} MB</span>
              <button onClick={() => player.play(url, m.name)}>Play</button>
              <a className="button" href={url} download>
                Download
              </a>
            </li>
          );
        })}
      </ul>
      {project.plan && <p className="muted small">Total length of the current plan: {fmtDuration(project.plan.total_duration_s)}.</p>}
    </section>
  );
}
