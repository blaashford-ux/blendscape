import type { EqCurves, Project, Transition, TrackView } from "../src/api/types";

export const EQ: EqCurves = {
  bass_swap_center: 0.5,
  bass_swap_beats: 4,
  mid_in: [0.15, 0.6],
  mid_out: [0.45, 0.85],
  hi_in: [0, 0.4],
  hi_out: [0.6, 1],
};

export function track(i: number, over: Partial<TrackView> = {}): TrackView {
  return {
    id: `t_${i}`,
    name: `song${i}.wav`,
    source: "upload",
    duration_s: 200,
    samplerate: 44100,
    channels: 2,
    downbeat_override: null,
    analysed: true,
    missing: false,
    analysis: {
      bpm: 120 + i,
      offset_s: 0.3,
      downbeat: 1,
      downbeat_confidence: 0.9,
      downbeat_probs: [0.03, 0.9, 0.04, 0.03],
      downbeat_manual: false,
      sections: [
        { label: "intro", start_bar: 0, end_bar: 8 },
        { label: "body", start_bar: 8, end_bar: 40 },
        { label: "outro", start_bar: 40, end_bar: 48 },
      ],
      active_end_s: 198,
    },
    ...over,
  };
}

export function transition(i: number, over: Partial<Transition> = {}): Transition {
  return {
    index: i,
    from: `t_${i}`,
    to: `t_${i + 1}`,
    style: "bass_swap",
    overlap_beats: 32,
    overlap_s: 16,
    out_beat: 160,
    in_beat: 1,
    out_time_s: 80.3,
    in_time_s: 0.8,
    start_s: 80.3,
    end_s: 96.3,
    unplayed_tail_s: 0,
    tempo_gap: 0.02,
    score: 0.87,
    note: "A leaves at the start of its outro",
    eq: EQ,
    warnings: [],
    alternatives: [
      { out_beat: 128, in_beat: 1, score: 0.8, note: "A leaves from the middle of a body", out_time_s: 64.3, in_time_s: 0.8 },
      { out_beat: 160, in_beat: 33, score: 0.7, note: "B enters at a section start", out_time_s: 80.3, in_time_s: 16.3 },
    ],
    ...over,
  };
}

export function project(over: Partial<Project> = {}, nTracks = 3): Project {
  const tracks = Array.from({ length: nTracks }, (_, i) => track(i));
  return {
    id: "p_abc123",
    name: "Test mix",
    created: 1,
    updated: 2,
    settings: {
      overlap_beats: 32,
      overlap_seconds: null,
      style: "bass_swap",
      phrase_bars: 8,
      mix_points: "smart",
      max_cut_seconds: 10,
      sample_rate: 44100,
      bit_depth: 24,
      ceiling_db: -1,
      limiter: { enabled: true, true_peak: true, lookahead_ms: 3, release_ms: 120 },
    },
    tracks,
    plan: {
      sample_rate: 44100,
      total_duration_s: 300,
      tracks: tracks.map((t, i) => ({
        id: t.id,
        name: t.name,
        bpm: 120 + i,
        downbeat: 1,
        confidence: 0.9,
        duration_s: 200,
        end_s: 198,
        offset_s: 0.3,
        sections: t.analysis!.sections,
      })),
      transitions: Array.from({ length: nTracks - 1 }, (_, i) => transition(i, { start_s: 80.3 + 80 * i, end_s: 96.3 + 80 * i })),
    },
    plan_stale: false,
    plan_warnings: [],
    mixes: [],
    ...over,
  };
}

export function peaks() {
  return { points: 100, min: Array(100).fill(-0.5), max: Array(100).fill(0.5), duration_s: 200, sample_rate: 44100 };
}
