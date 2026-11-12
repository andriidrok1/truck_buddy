// Radar frames for the weather view: asks the backend for the latest frame while the view is on (every
// RADAR_POLL_MS, and once when it is switched on), never otherwise. Failures keep the last frame.

import { useEffect, useState } from "react";

import { api } from "../api";
import { radarOverlay, type RadarFrame, type RadarOverlay } from "../map/mapView";
import { absoluteUrl } from "../url";

export const RADAR_POLL_MS = 5 * 60 * 1000;

export function useRadar(on: boolean): { frame: RadarFrame | null; overlay: RadarOverlay | null } {
  const [frame, setFrame] = useState<RadarFrame | null>(null);
  useEffect(() => {
    if (!on) return;
    let alive = true;
    const load = () =>
      api
        .radarFrame()
        .then((f) => {
          if (alive) setFrame(f);
        })
        .catch(() => {});
    load();
    const id = setInterval(load, RADAR_POLL_MS);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [on]);
  return { frame, overlay: on ? radarOverlay(frame, absoluteUrl(""), null) : null };
}
