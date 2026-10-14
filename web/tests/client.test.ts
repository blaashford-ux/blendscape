import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError, uploadTrack } from "../src/api/client";

function mockFetch(status: number, body: unknown, statusText = "") {
  const fn = vi.fn(async () => new Response(status === 204 ? null : JSON.stringify(body), { status, statusText }));
  vi.stubGlobal("fetch", fn);
  return fn;
}
afterEach(() => vi.unstubAllGlobals());

describe("api client", () => {
  it("sends JSON and returns the parsed body", async () => {
    const f = mockFetch(200, { id: "p_1" });
    const out = await api.createProject("Friday");
    expect(out).toEqual({ id: "p_1" });
    expect(f).toHaveBeenCalledWith("/api/projects", expect.objectContaining({ method: "POST", body: JSON.stringify({ name: "Friday" }) }));
    expect((f.mock.calls[0] as unknown as [string, RequestInit])[1].headers).toEqual({ "Content-Type": "application/json" });
  });
  it("sends no body or content type for GET and DELETE", async () => {
    const f = mockFetch(200, []);
    await api.listProjects();
    expect((f.mock.calls[0] as unknown as [string, RequestInit])[1]).toMatchObject({ method: "GET", body: undefined, headers: undefined });
  });
  it("returns undefined for 204", async () => {
    mockFetch(204, null);
    await expect(api.deleteProject("p_1")).resolves.toBeUndefined();
  });
  it("turns an error body into an ApiError with the detail and the list of problems", async () => {
    mockFetch(422, { detail: "invalid plan", errors: ["a runs past the end", "b collides"] });
    const err = await api.patchTransition("p_1", 0, { out_beat: 9 }).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ status: 422, message: "invalid plan", errors: ["a runs past the end", "b collides"] });
  });
  it("copes with FastAPI validation errors and non-JSON failures", async () => {
    mockFetch(422, { detail: [{ msg: "Input should be at least 2" }, { msg: "bad style" }] });
    await expect(api.patchSettings("p_1", { overlap_beats: 1 })).rejects.toThrow("Input should be at least 2; bad style");
    vi.stubGlobal("fetch", vi.fn(async () => new Response("<html>oops</html>", { status: 502, statusText: "Bad Gateway" })));
    await expect(api.health()).rejects.toMatchObject({ status: 502, message: "502 Bad Gateway" });
  });
  it("encodes ids and builds the media urls", () => {
    expect(api.audioUrl("p_1", "t_2")).toBe("/api/projects/p_1/tracks/t_2/audio");
    expect(api.clickUrl("p_1", "t_2")).toBe("/api/projects/p_1/tracks/t_2/click?seconds=30");
    expect(api.clickUrl("p_1", "t_2", 12.5, 10)).toBe("/api/projects/p_1/tracks/t_2/click?seconds=10&start=12.5");
  });
  it("uses the documented routes and verbs", async () => {
    const f = mockFetch(200, {});
    const calls: [() => Promise<unknown>, string, string][] = [
      [() => api.reorder("p", ["a", "b"]), "PUT", "/api/projects/p/tracks/order"],
      [() => api.setDownbeat("p", "t", 3), "PUT", "/api/projects/p/tracks/t/downbeat"],
      [() => api.analyze("p"), "POST", "/api/projects/p/analyze"],
      [() => api.plan("p"), "POST", "/api/projects/p/plan"],
      [() => api.useAlternative("p", 1, 2), "POST", "/api/projects/p/plan/transitions/1/alternative"],
      [() => api.resetTransition("p", 1), "DELETE", "/api/projects/p/plan/transitions/1/override"],
      [() => api.preview("p", 0), "POST", "/api/projects/p/plan/transitions/0/preview"],
      [() => api.render("p"), "POST", "/api/projects/p/render"],
      [() => api.cancelJob("j_1"), "DELETE", "/api/jobs/j_1"],
    ];
    for (const [call, method, url] of calls) {
      f.mockClear();
      await call();
      expect(f).toHaveBeenCalledWith(url, expect.objectContaining({ method }));
    }
  });
});

describe("uploadTrack", () => {
  class FakeXHR {
    static last: FakeXHR;
    upload: { onprogress?: (e: { lengthComputable: boolean; loaded: number; total: number }) => void } = {};
    onload?: () => void;
    onerror?: () => void;
    status = 201;
    statusText = "Created";
    responseText = "";
    method = "";
    url = "";
    sent: FormData | null = null;
    constructor() {
      FakeXHR.last = this;
    }
    open(m: string, u: string) {
      this.method = m;
      this.url = u;
    }
    send(f: FormData) {
      this.sent = f;
    }
  }
  const file = () => new File(["abc"], "song.wav", { type: "audio/wav" });

  it("posts the file as multipart and reports progress", async () => {
    vi.stubGlobal("XMLHttpRequest", FakeXHR);
    const seen: number[] = [];
    const p = uploadTrack("p_1", file(), (f) => seen.push(f));
    const x = FakeXHR.last;
    expect(x.method).toBe("POST");
    expect(x.url).toBe("/api/projects/p_1/tracks/upload");
    expect((x.sent!.get("file") as File).name).toBe("song.wav");
    x.upload.onprogress?.({ lengthComputable: true, loaded: 1, total: 4 });
    x.upload.onprogress?.({ lengthComputable: false, loaded: 0, total: 0 });
    x.upload.onprogress?.({ lengthComputable: true, loaded: 4, total: 4 });
    x.responseText = JSON.stringify({ id: "t_9", name: "song.wav" });
    x.onload?.();
    await expect(p).resolves.toMatchObject({ id: "t_9" });
    expect(seen).toEqual([0.25, 1]);
  });
  it("rejects with the server's explanation", async () => {
    vi.stubGlobal("XMLHttpRequest", FakeXHR);
    const p = uploadTrack("p_1", file());
    const x = FakeXHR.last;
    x.status = 422;
    x.responseText = JSON.stringify({ detail: "song.wav is not an audio file" });
    x.onload?.();
    await expect(p).rejects.toMatchObject({ status: 422, message: "song.wav is not an audio file" });
  });
  it("reports a network failure", async () => {
    vi.stubGlobal("XMLHttpRequest", FakeXHR);
    const p = uploadTrack("p_1", file());
    FakeXHR.last.onerror?.();
    await expect(p).rejects.toThrow("Could not reach the Blendscape server.");
  });
});
