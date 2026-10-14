# SPDX-License-Identifier: GPL-3.0-or-later
"""Background jobs with progress events (analysis, previews, final renders).

Work runs on a small thread pool. A job's function gets a :class:`JobContext`; calling ``ctx.report(...)`` records a
progress event and is also where a cancel request takes effect, so cancellation is cooperative and lands between
stages (a track finishing, a render step), never in the middle of a Rubber Band call."""
from __future__ import annotations

import threading
import time
import traceback
import uuid
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

QUEUED, RUNNING, DONE, ERROR, CANCELLED = "queued", "running", "done", "error", "cancelled"
TERMINAL = (DONE, ERROR, CANCELLED)
KEEP_JOBS = 200


class JobCancelled(Exception):
    pass


@dataclass
class Event:
    seq: int
    state: str
    message: str
    fraction: float | None
    time: float


@dataclass
class Job:
    id: str
    kind: str
    project_id: str | None
    state: str = QUEUED
    message: str = ""
    fraction: float | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    created: float = field(default_factory=time.time)
    started: float | None = None
    finished: float | None = None
    cancel_requested: bool = False
    events: list[Event] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"id": self.id, "kind": self.kind, "project_id": self.project_id, "state": self.state,
                "message": self.message, "fraction": self.fraction, "result": self.result, "error": self.error,
                "created": self.created, "started": self.started, "finished": self.finished}


class JobContext:
    def __init__(self, manager: JobManager, job: Job):
        self._m, self.job = manager, job

    def report(self, message: str, fraction: float | None = None) -> None:
        """Record progress; raises :class:`JobCancelled` if the job was cancelled."""
        if self.job.cancel_requested:
            raise JobCancelled()
        self._m._event(self.job, message=message, fraction=fraction)


class JobManager:
    def __init__(self, workers: int = 2):
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="blendscape-job")
        self._jobs: dict[str, Job] = {}
        self._cond = threading.Condition()

    # ---------- submitting ----------
    def submit(self, kind: str, fn: Callable[[JobContext], dict | None], project_id: str | None = None) -> Job:
        job = Job(id="j_" + uuid.uuid4().hex[:10], kind=kind, project_id=project_id)
        with self._cond:
            self._jobs[job.id] = job
            self._prune()
        self._event(job, message="queued")
        self._pool.submit(self._run, job, fn)
        return job

    def _run(self, job: Job, fn: Callable[[JobContext], dict | None]) -> None:
        if job.cancel_requested:
            self._finish(job, CANCELLED, message="cancelled before it started")
            return
        job.started = time.time()
        self._event(job, state=RUNNING, message="started")
        try:
            result = fn(JobContext(self, job))
        except JobCancelled:
            self._finish(job, CANCELLED, message="cancelled")
        except Exception as e:  # report every failure to the client; the traceback goes to the server log
            traceback.print_exc()
            self._finish(job, ERROR, message=str(e) or e.__class__.__name__, error=str(e) or e.__class__.__name__)
        else:
            job.result = result
            self._finish(job, DONE, message="done", fraction=1.0)

    def _finish(self, job: Job, state: str, message: str, error: str | None = None, fraction: float | None = None):
        job.error = error
        job.finished = time.time()
        self._event(job, state=state, message=message, fraction=fraction if fraction is not None else job.fraction)

    def _event(self, job: Job, state: str | None = None, message: str | None = None,
               fraction: float | None = None) -> None:
        with self._cond:
            if state:
                job.state = state
            if message is not None:
                job.message = message
            if fraction is not None:
                job.fraction = fraction
            job.events.append(Event(len(job.events), job.state, job.message, job.fraction, time.time()))
            self._cond.notify_all()

    # ---------- reading ----------
    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def list(self, project_id: str | None = None) -> list[Job]:
        jobs = [j for j in self._jobs.values() if project_id in (None, j.project_id)]
        return sorted(jobs, key=lambda j: j.created, reverse=True)

    def cancel(self, job_id: str) -> Job | None:
        job = self._jobs.get(job_id)
        if job and job.state not in TERMINAL:
            job.cancel_requested = True
            self._event(job, message="cancel requested")
        return job

    def stream(self, job_id: str, keepalive_s: float = 15.0) -> Iterator[Event | None]:
        """Yield every event of a job (past ones first) until it finishes; ``None`` means "still alive, nothing
        new" so the caller can send a keep-alive."""
        job = self._jobs.get(job_id)
        if job is None:
            return
        seen = 0
        while True:
            with self._cond:
                if seen >= len(job.events) and job.state not in TERMINAL:
                    self._cond.wait(timeout=keepalive_s)
                new = job.events[seen:]
                done = job.state in TERMINAL and seen + len(new) >= len(job.events)
            if not new and not done:
                yield None
                continue
            yield from new
            seen += len(new)
            if done:
                return

    def _prune(self) -> None:
        if len(self._jobs) <= KEEP_JOBS:
            return
        old = sorted((j for j in self._jobs.values() if j.state in TERMINAL), key=lambda j: j.created)
        for j in old[: len(self._jobs) - KEEP_JOBS]:
            del self._jobs[j.id]

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
