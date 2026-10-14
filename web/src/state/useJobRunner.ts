import { useCallback, useEffect, useRef, useState } from "react";
import { followJob, JobFailed, type FollowedJob } from "../api/jobs";
import type { JobEvent, JobInfo } from "../api/types";

export interface JobRun {
  label: string;
  state: "running" | "done" | "error" | "cancelled";
  message: string;
  fraction: number | null;
  error?: string;
  result?: Record<string, unknown> | null;
}

/**
 * Runs one background job at a time and exposes its live progress. `start` resolves with the final event, or `null`
 * if the job failed or was cancelled (the failure is in `run`, so callers only handle the success path).
 */
export function useJobRunner() {
  const [run, setRun] = useState<JobRun | null>(null);
  const followed = useRef<FollowedJob | null>(null);
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      followed.current?.close();
    };
  }, []);

  const start = useCallback(async (label: string, begin: () => Promise<JobInfo>): Promise<JobEvent | null> => {
    followed.current?.close();
    setRun({ label, state: "running", message: "starting…", fraction: null });
    let job: JobInfo;
    try {
      job = await begin();
    } catch (e) {
      if (alive.current) setRun({ label, state: "error", message: "", fraction: null, error: (e as Error).message });
      return null;
    }
    const f = followJob(job, (ev) => {
      if (alive.current && ev.state === "running") {
        setRun({ label, state: "running", message: ev.message, fraction: ev.fraction });
      }
    });
    followed.current = f;
    try {
      const final = await f.done;
      if (alive.current) setRun({ label, state: "done", message: "done", fraction: 1, result: final.result });
      return final;
    } catch (e) {
      if (alive.current) {
        const cancelled = e instanceof JobFailed && e.cancelled;
        setRun({
          label,
          state: cancelled ? "cancelled" : "error",
          message: cancelled ? "cancelled" : "",
          fraction: null,
          error: cancelled ? undefined : (e as Error).message,
        });
      }
      return null;
    }
  }, []);

  const cancel = useCallback(() => followed.current?.cancel(), []);
  const dismiss = useCallback(() => setRun(null), []);
  return { run, start, cancel, dismiss, running: run?.state === "running" };
}
