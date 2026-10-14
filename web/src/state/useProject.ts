import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { Peaks, Project } from "../api/types";

/** Load one project and keep it fresh. `apply` takes the project an API call returned, so edits show immediately. */
export function useProject(id: string) {
  const [project, setProject] = useState<Project | null>(null);
  const [error, setError] = useState<string | null>(null);
  const current = useRef(id);
  current.current = id;

  const refresh = useCallback(async () => {
    try {
      const p = await api.getProject(id);
      if (current.current === id) {
        setProject(p);
        setError(null);
      }
      return p;
    } catch (e) {
      if (current.current === id) setError((e as Error).message);
      return null;
    }
  }, [id]);

  const apply = useCallback((p: Project) => {
    if (current.current === p.id) setProject(p);
  }, []);

  useEffect(() => {
    setProject(null);
    void refresh();
  }, [id, refresh]);

  return { project, error, refresh, apply };
}

const peaksCache = new Map<string, Promise<Peaks>>();

/** Waveform peaks for a track, fetched once and shared by every component that draws it. */
export function usePeaks(projectId: string, trackId: string | undefined, points = 6000): Peaks | null {
  const [peaks, setPeaks] = useState<Peaks | null>(null);
  useEffect(() => {
    setPeaks(null);
    if (!trackId) return;
    const key = `${projectId}/${trackId}/${points}`;
    let p = peaksCache.get(key);
    if (!p) {
      p = api.waveform(projectId, trackId, points);
      peaksCache.set(key, p);
      p.catch(() => peaksCache.delete(key)); // a failed fetch must be retried next time
    }
    let live = true;
    p.then((v) => live && setPeaks(v)).catch(() => undefined);
    return () => {
      live = false;
    };
  }, [projectId, trackId, points]);
  return peaks;
}

export function clearPeaksCache() {
  peaksCache.clear();
}
