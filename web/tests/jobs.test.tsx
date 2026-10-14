import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { followJob, JobFailed } from "../src/api/jobs";
import type { JobEvent, JobInfo } from "../src/api/types";
import { useJobRunner } from "../src/state/useJobRunner";

class FakeES {
  static last: FakeES;
  listeners: Record<string, ((m: MessageEvent) => void)[]> = {};
  closed = false;
  readyState = 1;
  onerror: (() => void) | null = null;
  static CLOSED = 2;
  constructor(public url: string) {
    FakeES.last = this;
  }
  addEventListener(type: string, fn: (m: MessageEvent) => void) {
    (this.listeners[type] ??= []).push(fn);
  }
  close() {
    this.closed = true;
    this.readyState = 2;
  }
  emit(ev: Partial<JobEvent>) {
    const full: JobEvent = { seq: 0, state: "running", message: "", fraction: null, ...ev };
    this.listeners.progress?.forEach((f) => f({ data: JSON.stringify(full) } as MessageEvent));
  }
}

const job = (over: Partial<JobInfo> = {}): JobInfo => ({ id: "j_1", kind: "analyze", project_id: "p_1", state: "queued", message: "", fraction: null, result: null, error: null, ...over });

beforeEach(() => vi.stubGlobal("EventSource", FakeES));
afterEach(() => vi.unstubAllGlobals());

describe("followJob", () => {
  it("passes progress along and resolves with the final event", async () => {
    const seen: string[] = [];
    const f = followJob(job(), (e) => seen.push(e.message));
    expect(FakeES.last.url).toBe("/api/jobs/j_1/events");
    FakeES.last.emit({ message: "step 1", fraction: 0.5 });
    FakeES.last.emit({ state: "done", message: "done", fraction: 1, result: { n: 3 } });
    await expect(f.done).resolves.toMatchObject({ state: "done", result: { n: 3 } });
    expect(seen).toEqual(["step 1", "done"]);
    expect(FakeES.last.closed).toBe(true);
  });
  it("rejects with the server's error message", async () => {
    const f = followJob(job(), () => undefined);
    FakeES.last.emit({ state: "error", message: "x", error: "the file is on fire" });
    await expect(f.done).rejects.toThrow("the file is on fire");
  });
  it("rejects as cancelled when the job was cancelled", async () => {
    const f = followJob(job(), () => undefined);
    FakeES.last.emit({ state: "cancelled", message: "cancelled" });
    const err = await f.done.catch((e) => e);
    expect(err).toBeInstanceOf(JobFailed);
    expect(err.cancelled).toBe(true);
  });
  it("ignores events after it has been closed", () => {
    const seen: string[] = [];
    const f = followJob(job(), (e) => seen.push(e.message));
    f.close();
    FakeES.last.emit({ message: "late" });
    expect(seen).toEqual([]);
  });
  it("cancel asks the server to stop the job", () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(job()), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    followJob(job(), () => undefined).cancel();
    expect(fetchMock).toHaveBeenCalledWith("/api/jobs/j_1", expect.objectContaining({ method: "DELETE" }));
  });
  it("falls back to polling when EventSource is unavailable", async () => {
    vi.stubGlobal("EventSource", undefined);
    const states = ["running", "done"];
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify(job({ state: states.shift() as "running" | "done", message: "m", fraction: 0.5, result: { ok: 1 } })), { status: 200 })),
    );
    const f = followJob(job(), () => undefined);
    await expect(f.done).resolves.toMatchObject({ state: "done", result: { ok: 1 } });
  });
});

describe("useJobRunner", () => {
  it("shows progress and ends in done", async () => {
    vi.stubGlobal("fetch", vi.fn());
    const { result } = renderHook(() => useJobRunner());
    let outcome: JobEvent | null = null;
    let finished!: Promise<void>;
    await act(async () => {
      finished = result.current.start("Analysing", async () => job()).then((e) => void (outcome = e));
      await Promise.resolve();
    });
    expect(result.current.running).toBe(true);
    expect(result.current.run).toMatchObject({ label: "Analysing", state: "running" });
    await act(async () => {
      FakeES.last.emit({ message: "analysing a.wav (1/2)", fraction: 0.5 });
    });
    expect(result.current.run).toMatchObject({ message: "analysing a.wav (1/2)", fraction: 0.5 });
    await act(async () => {
      FakeES.last.emit({ state: "done", message: "done", fraction: 1, result: { planned: true } });
      await finished;
    });
    expect(outcome).toMatchObject({ state: "done" });
    expect(result.current.run).toMatchObject({ state: "done", result: { planned: true } });
    expect(result.current.running).toBe(false);
    act(() => result.current.dismiss());
    expect(result.current.run).toBeNull();
  });
  it("reports a job that fails to start, and one that fails while running, without throwing", async () => {
    const { result } = renderHook(() => useJobRunner());
    let out: unknown = "unset";
    await act(async () => {
      out = await result.current.start("Rendering", async () => {
        throw new Error("Rubber Band is not installed");
      });
    });
    expect(out).toBeNull();
    expect(result.current.run).toMatchObject({ state: "error", error: "Rubber Band is not installed" });

    let p!: Promise<unknown>;
    await act(async () => {
      p = result.current.start("Rendering", async () => job());
      await Promise.resolve();
    });
    await act(async () => {
      FakeES.last.emit({ state: "error", message: "x", error: "boom" });
      await p;
    });
    expect(result.current.run).toMatchObject({ state: "error", error: "boom" });
  });
  it("marks cancelled jobs as cancelled, not as errors", async () => {
    const { result } = renderHook(() => useJobRunner());
    let p!: Promise<unknown>;
    await act(async () => {
      p = result.current.start("Rendering", async () => job());
      await Promise.resolve();
    });
    await act(async () => {
      FakeES.last.emit({ state: "cancelled", message: "cancelled" });
      await p;
    });
    expect(result.current.run).toMatchObject({ state: "cancelled" });
    expect(result.current.run?.error).toBeUndefined();
  });
});
