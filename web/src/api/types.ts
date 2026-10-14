// Shapes returned by the Blendscape API (see docs/API.md). Kept in one place so a change in the server shows up as a
// type error here.

export type Style = "bass_swap" | "crossfade";

export interface LimiterSettings {
  enabled: boolean;
  true_peak: boolean;
  lookahead_ms: number;
  release_ms: number;
}

export interface Settings {
  overlap_beats: number;
  overlap_seconds: number | null;
  style: Style;
  phrase_bars: number;
  mix_points: "smart" | "latest";
  max_cut_seconds: number;
  sample_rate: number;
  bit_depth: 16 | 24 | 32;
  ceiling_db: number;
  limiter: LimiterSettings;
}

export type SettingsPatch = Partial<Omit<Settings, "limiter">> & {
  limiter?: Partial<LimiterSettings>;
  clear_overlap_seconds?: boolean;
};

export interface Section {
  label: "intro" | "body" | "breakdown" | "outro";
  start_bar: number;
  end_bar: number;
}

export interface TrackAnalysis {
  bpm: number;
  offset_s: number;
  downbeat: number;
  downbeat_confidence: number;
  downbeat_probs: number[];
  downbeat_manual: boolean;
  sections: Section[];
  active_end_s: number;
}

export interface TrackView {
  id: string;
  name: string;
  source: "path" | "upload";
  duration_s: number;
  samplerate: number;
  channels: number;
  downbeat_override: number | null;
  analysed: boolean;
  missing: boolean;
  analysis?: TrackAnalysis;
}

export interface PlanTrack {
  id: string;
  name: string;
  bpm: number;
  downbeat: number;
  confidence: number;
  duration_s: number;
  end_s: number;
  offset_s: number;
  sections: Section[];
}

export interface EqCurves {
  bass_swap_center: number;
  bass_swap_beats: number;
  mid_in: [number, number];
  mid_out: [number, number];
  hi_in: [number, number];
  hi_out: [number, number];
}

export interface Alternative {
  out_beat: number;
  in_beat: number;
  score: number;
  note: string;
  out_time_s: number;
  in_time_s: number;
}

export interface Transition {
  index: number;
  from: string;
  to: string;
  style: Style;
  overlap_beats: number;
  overlap_s: number;
  out_beat: number;
  in_beat: number;
  out_time_s: number; // where the overlap starts in the outgoing file
  in_time_s: number; // where the incoming file enters
  start_s: number; // overlap start on the mix clock
  end_s: number;
  unplayed_tail_s: number;
  tempo_gap: number;
  score: number | null;
  note: string;
  eq: EqCurves;
  warnings: string[];
  alternatives: Alternative[];
}

export interface PlanDescription {
  tracks: PlanTrack[];
  transitions: Transition[];
  total_duration_s: number;
  sample_rate: number;
}

export interface MixFile {
  name: string;
  size: number;
  modified: number;
}

export interface Project {
  id: string;
  name: string;
  created: number;
  updated: number;
  settings: Settings;
  tracks: TrackView[];
  plan: PlanDescription | null;
  plan_stale: boolean;
  plan_warnings: string[];
  mixes: MixFile[];
}

export interface ProjectSummary {
  id: string;
  name: string;
  created: number;
  updated: number;
  tracks: number;
}

export type JobState = "queued" | "running" | "done" | "error" | "cancelled";

export interface JobInfo {
  id: string;
  kind: string;
  project_id: string | null;
  state: JobState;
  message: string;
  fraction: number | null;
  result: Record<string, unknown> | null;
  error: string | null;
}

export interface JobEvent {
  seq: number;
  state: JobState;
  message: string;
  fraction: number | null;
  result?: Record<string, unknown> | null;
  error?: string | null;
}

export interface Peaks {
  points: number;
  min: number[];
  max: number[];
  duration_s: number;
  sample_rate: number;
}

export interface PreviewResult {
  url: string;
  duration_s: number;
  overlap_start_s: number;
  overlap_end_s: number;
  peak_db: number;
  max_reduction_db: number;
}

export interface RenderResult {
  url: string;
  name: string;
  duration_s: number;
  peak_db: number;
  max_reduction_db: number;
  limited_fraction: number;
  clipped: boolean;
}

export interface TransitionPatch {
  out_beat?: number;
  in_beat?: number;
  overlap_beats?: number;
  style?: Style;
  eq?: Partial<EqCurves>;
}
