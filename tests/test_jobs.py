# SPDX-License-Identifier: GPL-3.0-or-later
import threading
import time

import pytest

from blendscape.server.jobs import CANCELLED, DONE, ERROR, QUEUED, RUNNING, JobCancelled, JobManager


@pytest.fixture
def jm():
    m = JobManager(workers=2)
    yield m
    m.shutdown()


def wait(job, states=(DONE, ERROR, CANCELLED), timeout=10):
    t = time.time()
    while job.state not in states:
        assert time.time() - t < timeout, f"job stuck in {job.state}"
        time.sleep(0.01)
    return job


def test_a_job_runs_and_returns_its_result(jm):
    job = jm.submit("demo", lambda ctx: {"answer": 42}, project_id="p_1")
    wait(job)
    assert job.state == DONE and job.result == {"answer": 42} and job.error is None
    assert job.fraction == 1.0 and job.started and job.finished >= job.started
    assert jm.get(job.id) is job and job in jm.list("p_1") and job not in jm.list("p_2")


def test_progress_is_recorded_in_order_and_replayable(jm):
    def work(ctx):
        for i in range(3):
            ctx.report(f"step {i}", i / 3)

    job = wait(jm.submit("demo", work))
    msgs = [(e.state, e.message) for e in job.events]
    assert msgs[0] == (QUEUED, "queued") and msgs[1][0] == RUNNING
    assert [m for _s, m in msgs if m.startswith("step")] == ["step 0", "step 1", "step 2"]
    assert msgs[-1] == (DONE, "done")
    assert [e.seq for e in job.events] == list(range(len(job.events)))
    # a late subscriber still gets everything, then the stream ends
    replay = [e for e in jm.stream(job.id) if e is not None]
    assert [e.seq for e in replay] == [e.seq for e in job.events]


def test_failures_are_captured_not_raised(jm):
    def boom(ctx):
        raise ValueError("the file is on fire")

    job = wait(jm.submit("demo", boom))
    assert job.state == ERROR and job.error == "the file is on fire" and job.result is None
    assert job.events[-1].state == ERROR


def test_cancel_takes_effect_at_the_next_progress_report(jm):
    started, release = threading.Event(), threading.Event()

    def work(ctx):
        ctx.report("first")
        started.set()
        release.wait(5)
        ctx.report("second")  # cancellation lands here
        return {"never": True}

    job = jm.submit("demo", work)
    assert started.wait(5)
    jm.cancel(job.id)
    release.set()
    wait(job)
    assert job.state == CANCELLED and job.result is None


def test_cancelling_a_queued_job_never_runs_it():
    m = JobManager(workers=1)
    try:
        gate, ran = threading.Event(), []
        first = m.submit("a", lambda ctx: gate.wait(5))
        second = m.submit("b", lambda ctx: ran.append(1))
        m.cancel(second.id)
        gate.set()
        wait(first)
        wait(second)
        assert second.state == CANCELLED and not ran
    finally:
        m.shutdown()


def test_cancelling_a_finished_job_changes_nothing(jm):
    job = wait(jm.submit("demo", lambda ctx: {}))
    jm.cancel(job.id)
    assert job.state == DONE and not job.cancel_requested
    assert jm.cancel("j_nope") is None


def test_stream_follows_a_running_job_to_its_end(jm):
    go = threading.Event()

    def work(ctx):
        go.wait(5)
        ctx.report("late step", 0.5)

    job = jm.submit("demo", work)
    got = []

    def reader():
        for e in jm.stream(job.id, keepalive_s=0.05):
            got.append(e)

    t = threading.Thread(target=reader)
    t.start()
    time.sleep(0.3)  # the reader has caught up and is waiting: it must have seen keep-alives (None)
    go.set()
    t.join(5)
    assert not t.is_alive()
    assert None in got and got[-1].state == DONE
    assert any(e is not None and e.message == "late step" for e in got)
    assert list(jm.stream("j_missing")) == []


def test_old_finished_jobs_are_forgotten(jm, monkeypatch):
    from blendscape.server import jobs

    monkeypatch.setattr(jobs, "KEEP_JOBS", 5)
    made = [wait(jm.submit("demo", lambda ctx: {})) for _ in range(9)]
    assert len(jm.list()) <= 6
    assert jm.get(made[-1].id) is made[-1]


def test_cancel_exception_type_is_exported():
    assert issubclass(JobCancelled, Exception)
