import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

export interface PlayerRegion {
  start: number;
  end: number;
}

export interface PlayerState {
  src: string | null;
  title: string;
  playing: boolean;
  time: number;
  duration: number;
  region: PlayerRegion | null;
  error: string | null;
}

export interface PlayerApi extends PlayerState {
  /** Play `src` (optionally from `at` seconds). Everything in the app plays through this one player. */
  play: (src: string, title: string, opts?: { at?: number; region?: PlayerRegion }) => void;
  toggle: () => void;
  seek: (t: number) => void;
  stop: () => void;
}

const EMPTY: PlayerState = { src: null, title: "", playing: false, time: 0, duration: 0, region: null, error: null };
const Ctx = createContext<PlayerApi | null>(null);

export function PlayerProvider({ children }: { children: ReactNode }) {
  const audio = useRef<HTMLAudioElement | null>(null);
  const [state, setState] = useState<PlayerState>(EMPTY);

  useEffect(() => {
    const a = new Audio();
    a.preload = "metadata";
    audio.current = a;
    const sync = () =>
      setState((s) => ({ ...s, playing: !a.paused, time: a.currentTime, duration: Number.isFinite(a.duration) ? a.duration : s.duration }));
    const fail = () => setState((s) => ({ ...s, playing: false, error: "This audio could not be played." }));
    for (const ev of ["play", "pause", "timeupdate", "durationchange", "ended", "loadedmetadata"]) a.addEventListener(ev, sync);
    a.addEventListener("error", fail);
    return () => {
      a.pause();
      a.src = "";
    };
  }, []);

  const play = useCallback<PlayerApi["play"]>((src, title, opts) => {
    const a = audio.current;
    if (!a) return;
    const absolute = new URL(src, window.location.href).href;
    const start = () => {
      if (opts?.at !== undefined) a.currentTime = opts.at;
      void a.play().catch(() => setState((s) => ({ ...s, playing: false, error: "The browser blocked playback." })));
    };
    setState({ ...EMPTY, src, title, region: opts?.region ?? null });
    if (a.src !== absolute) {
      a.src = absolute;
      a.addEventListener("loadedmetadata", start, { once: true });
      a.load();
    } else start();
  }, []);

  const toggle = useCallback(() => {
    const a = audio.current;
    if (!a || !a.src) return;
    if (a.paused) void a.play();
    else a.pause();
  }, []);

  const seek = useCallback((t: number) => {
    if (audio.current) audio.current.currentTime = Math.max(0, t);
  }, []);

  const stop = useCallback(() => {
    const a = audio.current;
    if (a) {
      a.pause();
      a.removeAttribute("src");
      a.load();
    }
    setState(EMPTY);
  }, []);

  const api = useMemo<PlayerApi>(() => ({ ...state, play, toggle, seek, stop }), [state, play, toggle, seek, stop]);
  return <Ctx.Provider value={api}>{children}</Ctx.Provider>;
}

export function usePlayer(): PlayerApi {
  const v = useContext(Ctx);
  if (!v) throw new Error("usePlayer must be used inside <PlayerProvider>");
  return v;
}
