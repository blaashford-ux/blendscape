import type {
  AppConfig,
  JobInfo,
  Peaks,
  Project,
  ProjectSummary,
  Settings,
  SettingsPatch,
  TrackView,
  TransitionPatch,
} from "./types";

/** A failed API call. `errors` holds the individual problems when the server lists several (invalid plan edits). */
export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public errors: string[] = [],
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export const BASE = ""; // same origin: served by `blendscape serve`, or proxied by `npm run dev`

async function parseError(res: Response): Promise<ApiError> {
  let detail = `${res.status} ${res.statusText}`;
  let errors: string[] = [];
  try {
    const body = await res.json();
    if (typeof body.detail === "string") detail = body.detail;
    else if (Array.isArray(body.detail)) detail = body.detail.map((d: { msg?: string }) => d.msg ?? String(d)).join("; ");
    if (Array.isArray(body.errors)) errors = body.errors;
  } catch {
    /* not JSON */
  }
  return new ApiError(res.status, detail, errors);
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(BASE + path, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) throw await parseError(res);
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

const p = (id: string) => `/api/projects/${encodeURIComponent(id)}`;

export const api = {
  config: () => request<AppConfig>("GET", "/api/config"),
  setConfig: (c: { rubberband?: string | null; export_dir?: string | null }) => request<AppConfig>("PUT", "/api/config", c),
  health: () => request<{ ok: boolean; version: string; stretch_backend: string | null; home: string }>("GET", "/api/health"),

  listProjects: () => request<ProjectSummary[]>("GET", "/api/projects"),
  createProject: (name: string, settings?: Settings) => request<Project>("POST", "/api/projects", { name, settings }),
  getProject: (id: string) => request<Project>("GET", p(id)),
  renameProject: (id: string, name: string) => request<Project>("PATCH", p(id), { name }),
  deleteProject: (id: string) => request<void>("DELETE", p(id)),
  patchSettings: (id: string, patch: SettingsPatch) => request<Project>("PATCH", `${p(id)}/settings`, patch),

  addTrackByPath: (id: string, path: string) => request<TrackView>("POST", `${p(id)}/tracks`, { path }),
  deleteTrack: (id: string, tid: string) => request<void>("DELETE", `${p(id)}/tracks/${tid}`),
  reorder: (id: string, order: string[]) => request<Project>("PUT", `${p(id)}/tracks/order`, { order }),
  setDownbeat: (id: string, tid: string, downbeat: number | null) =>
    request<Project>("PUT", `${p(id)}/tracks/${tid}/downbeat`, { downbeat }),
  waveform: (id: string, tid: string, points = 6000) =>
    request<Peaks>("GET", `${p(id)}/tracks/${tid}/waveform?points=${points}`),
  audioUrl: (id: string, tid: string) => `${BASE}${p(id)}/tracks/${tid}/audio`,
  clickUrl: (id: string, tid: string, startS?: number, seconds = 30) =>
    `${BASE}${p(id)}/tracks/${tid}/click?seconds=${seconds}` + (startS === undefined ? "" : `&start=${startS}`),

  analyze: (id: string, opts: { replan?: boolean; force?: boolean } = {}) =>
    request<JobInfo>("POST", `${p(id)}/analyze`, opts),
  plan: (id: string) => request<Project>("POST", `${p(id)}/plan`),
  patchTransition: (id: string, i: number, patch: TransitionPatch) =>
    request<Project>("PATCH", `${p(id)}/plan/transitions/${i}`, patch),
  useAlternative: (id: string, i: number, index: number) =>
    request<Project>("POST", `${p(id)}/plan/transitions/${i}/alternative`, { index }),
  resetTransition: (id: string, i: number) => request<Project>("DELETE", `${p(id)}/plan/transitions/${i}/override`),
  preview: (id: string, i: number, leadS = 8, tailS = 8) =>
    request<JobInfo>("POST", `${p(id)}/plan/transitions/${i}/preview`, { lead_s: leadS, tail_s: tailS }),
  render: (id: string, name?: string) => request<JobInfo>("POST", `${p(id)}/render`, name ? { name } : {}),

  cancelJob: (jobId: string) => request<JobInfo>("DELETE", `/api/jobs/${jobId}`),
  getJob: (jobId: string) => request<JobInfo>("GET", `/api/jobs/${jobId}`),
};

/** Upload a file as a new track, reporting byte progress (fetch cannot, XHR can). */
export function uploadTrack(
  projectId: string,
  file: File,
  onProgress?: (fraction: number) => void,
): Promise<TrackView> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${BASE}${p(projectId)}/tracks/upload`);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress?.(e.loaded / e.total);
    };
    xhr.onerror = () => reject(new ApiError(0, "Could not reach the Blendscape server."));
    xhr.onload = () => {
      let body: { detail?: string } = {};
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        /* ignore */
      }
      if (xhr.status >= 200 && xhr.status < 300) resolve(body as unknown as TrackView);
      else reject(new ApiError(xhr.status, typeof body.detail === "string" ? body.detail : xhr.statusText));
    };
    const form = new FormData();
    form.append("file", file, file.name);
    xhr.send(form);
  });
}
