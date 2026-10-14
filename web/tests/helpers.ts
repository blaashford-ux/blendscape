import type { JobEvent, JobInfo } from "../src/api/types";

/** A stand-in for EventSource that tests can push events into. */
export class FakeES {
  static instances: FakeES[] = [];
  static CLOSED = 2;
  listeners: Record<string, ((m: MessageEvent) => void)[]> = {};
  closed = false;
  readyState = 1;
  onerror: (() => void) | null = null;
  constructor(public url: string) {
    FakeES.instances.push(this);
  }
  static get last() {
    return FakeES.instances[FakeES.instances.length - 1];
  }
  static reset() {
    FakeES.instances = [];
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

export const job = (over: Partial<JobInfo> = {}): JobInfo => ({
  id: "j_1",
  kind: "analyze",
  project_id: "p_abc123",
  state: "queued",
  message: "",
  fraction: null,
  result: null,
  error: null,
  ...over,
});

/** A stand-in for the browser's Audio element: tests decide when "loading" finishes. */
export class FakeAudio {
  static last: FakeAudio;
  listeners: Record<string, ((e?: unknown) => void)[]> = {};
  src = "";
  currentTime = 0;
  duration = NaN;
  paused = true;
  preload = "";
  playCalls = 0;
  loadCalls = 0;
  constructor() {
    FakeAudio.last = this;
  }
  static install() {
    (globalThis as { Audio?: unknown }).Audio = FakeAudio;
  }
  addEventListener(t: string, fn: (e?: unknown) => void, opts?: { once?: boolean }) {
    const wrapped = opts?.once ? (e?: unknown) => { this.listeners[t] = (this.listeners[t] ?? []).filter((f) => f !== wrapped); fn(e); } : fn;
    (this.listeners[t] ??= []).push(wrapped);
  }
  private fire(t: string) {
    [...(this.listeners[t] ?? [])].forEach((f) => f());
  }
  loaded(duration: number) {
    this.duration = duration;
    this.fire("loadedmetadata");
    this.fire("durationchange");
  }
  fail() {
    this.fire("error");
  }
  load() {
    this.loadCalls++;
  }
  play() {
    this.playCalls++;
    this.paused = false;
    this.fire("play");
    return Promise.resolve();
  }
  pause() {
    this.paused = true;
    this.fire("pause");
  }
  removeAttribute() {
    this.src = "";
  }
}
