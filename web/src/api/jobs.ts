import { api } from "./client";
import type { JobEvent, JobInfo } from "./types";

export interface FollowedJob {
  /** Resolves with the final event when the job is done; rejects with its error, or "cancelled". */
  done: Promise<JobEvent>;
  /** Ask the server to stop the job; `done` then rejects with "cancelled". */
  cancel: () => void;
  /** Stop listening without cancelling the job. */
  close: () => void;
}

export class JobFailed extends Error {
  constructor(
    message: string,
    public cancelled = false,
  ) {
    super(message);
    this.name = "JobFailed";
  }
}

/**
 * Follow a job's progress with server-sent events (past events are replayed by the server, so connecting late is
 * fine). Falls back to polling if EventSource is unavailable or the stream drops.
 */
export function followJob(job: JobInfo, onEvent: (e: JobEvent) => void): FollowedJob {
  let closed = false;
  let es: EventSource | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;

  const done = new Promise<JobEvent>((resolve, reject) => {
    const finish = (e: JobEvent) => {
      if (closed) return;
      closed = true;
      es?.close();
      if (timer) clearTimeout(timer);
      if (e.state === "done") resolve(e);
      else if (e.state === "cancelled") reject(new JobFailed("cancelled", true));
      else reject(new JobFailed(e.error ?? e.message ?? "the job failed"));
    };
    const handle = (e: JobEvent) => {
      if (closed) return;
      onEvent(e);
      if (e.state === "done" || e.state === "error" || e.state === "cancelled") finish(e);
    };

    const poll = async () => {
      if (closed) return;
      try {
        const j = await api.getJob(job.id);
        handle({ seq: -1, state: j.state, message: j.message, fraction: j.fraction, result: j.result, error: j.error });
      } catch (err) {
        if (!closed) {
          closed = true;
          reject(err);
        }
        return;
      }
      if (!closed) timer = setTimeout(poll, 500);
    };

    if (typeof EventSource === "undefined") {
      void poll();
      return;
    }
    es = new EventSource(`/api/jobs/${job.id}/events`);
    es.addEventListener("progress", (m) => handle(JSON.parse((m as MessageEvent).data) as JobEvent));
    es.onerror = () => {
      // the browser retries on its own; if the server is gone for good, polling will report the failure
      if (closed || es?.readyState !== EventSource.CLOSED) return;
      es = null;
      void poll();
    };
  });

  return {
    done,
    cancel: () => void api.cancelJob(job.id).catch(() => undefined),
    close: () => {
      closed = true;
      es?.close();
      if (timer) clearTimeout(timer);
    },
  };
}
