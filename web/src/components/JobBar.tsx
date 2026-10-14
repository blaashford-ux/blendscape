import type { JobRun } from "../state/useJobRunner";

interface Props {
  run: JobRun | null;
  onCancel: () => void;
  onDismiss: () => void;
}

/** Progress of the current background job (analysis, render, preview). */
export function JobBar({ run, onCancel, onDismiss }: Props) {
  if (!run) return null;
  const pct = run.fraction === null ? null : Math.round(run.fraction * 100);
  return (
    <div className={`job-bar job-${run.state}`} role="status" aria-live="polite">
      <div className="job-text">
        <strong>{run.label}</strong>
        <span>
          {run.state === "running" && run.message}
          {run.state === "done" && "Finished."}
          {run.state === "cancelled" && "Cancelled."}
          {run.state === "error" && <span className="error-text">{run.error ?? "Something went wrong."}</span>}
        </span>
      </div>
      {run.state === "running" && (
        <>
          <progress max={100} value={pct ?? undefined} aria-label={`${run.label} progress`} />
          <button onClick={onCancel}>Cancel</button>
        </>
      )}
      {run.state !== "running" && (
        <button onClick={onDismiss} aria-label="Dismiss">
          ✕
        </button>
      )}
    </div>
  );
}
