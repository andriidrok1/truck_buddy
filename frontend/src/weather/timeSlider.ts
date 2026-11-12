// The weather time slider (Figma V4): pick a moment between now and arrival, see the forecast radar for that
// moment, where the truck will be, and the weather there. Pure.

import type { RadarFrame } from "../map/mapView";
import type { LatLng } from "../types";
import type { Chip } from "./routeWeather";

/** Closer to now than this, the live radar is the better picture than a model forecast. */
export const LIVE_WINDOW_MIN = 10;
/** Past this many minutes ahead the forecast is rough: the track is drawn paler there. */
export const ROUGH_AFTER_MIN = 120;

/** Our radar layer for a moment: the live frame near now, else the HRRR step closest to it (steps count from
 *  the model run, which reaches us about 2 h late), null when the moment is past the forecast. */
export function layerAt(frame: RadarFrame | null, target: Date, now: Date): string | null {
  if (!frame?.enabled) return null;
  if ((target.getTime() - now.getTime()) / 60000 < LIVE_WINDOW_MIN) return frame.live ?? null;
  const init = frame.forecast_init ? Date.parse(frame.forecast_init) : NaN;
  const step = frame.forecast_step_min ?? 15;
  const max = frame.forecast_max_min ?? 0;
  if (Number.isNaN(init) || !max) return frame.live ?? null;
  const minute = Math.round((target.getTime() - init) / 60000 / step) * step;
  if (minute < 0) return frame.live ?? null;
  if (minute > max) return null;
  return `hrrr-f${String(minute).padStart(3, "0")}`;
}

/** The share (0..1) of the route's length before vertex `index`: where the truck already is. */
export function shareAt(geometry: [number, number][], index: number): number {
  if (geometry.length < 2) return 0;
  let before = 0;
  let total = 0;
  for (let i = 1; i < geometry.length; i++) {
    const d = Math.hypot(geometry[i][0] - geometry[i - 1][0], (geometry[i][1] - geometry[i - 1][1]) * Math.cos((geometry[i][0] * Math.PI) / 180));
    total += d;
    if (i <= index) before += d;
  }
  return total > 0 ? before / total : 0;
}

/** The point at `fraction` (0..1) of the route's length. */
export function positionAt(geometry: [number, number][], fraction: number): LatLng | null {
  if (!geometry.length) return null;
  if (geometry.length === 1 || fraction <= 0) return { lat: geometry[0][0], lng: geometry[0][1] };
  const seg = geometry.slice(1).map((p, i) => Math.hypot(p[0] - geometry[i][0], (p[1] - geometry[i][1]) * Math.cos((p[0] * Math.PI) / 180)));
  const total = seg.reduce((a, b) => a + b, 0);
  let left = Math.min(1, fraction) * total;
  for (let i = 0; i < seg.length; i++) {
    if (left <= seg[i] || i === seg.length - 1) {
      const k = seg[i] > 0 ? Math.min(1, left / seg[i]) : 0;
      const [a, b] = [geometry[i], geometry[i + 1]];
      return { lat: a[0] + (b[0] - a[0]) * k, lng: a[1] + (b[1] - a[1]) * k };
    }
    left -= seg[i];
  }
  const last = geometry[geometry.length - 1];
  return { lat: last[0], lng: last[1] };
}

/** The forecast point whose ETA is closest to the moment (what the slider's header shows). */
export function chipAt(list: readonly Chip[], target: Date): Chip | null {
  let best: Chip | null = null;
  let gap = Infinity;
  for (const c of list) {
    const d = Math.abs(Date.parse(c.eta) - target.getTime());
    if (d < gap) {
      gap = d;
      best = c;
    }
  }
  return best;
}

export const TRACK_COLOR: Record<string, string> = {
  ice: "#7FD4E4", snow: "#5B8DEF", wind: "#B9A9FF", fog: "#C7C7CC", rain: "#3CB043", cloud: "#C7C7CC", clear: "#C7C7CC",
};

export type TrackPart = { from: number; to: number; color: string; warn: boolean; rough: boolean };

/** The track as parts between the minutes of consecutive points, colored by what the truck meets (Figma V4). */
export function trackParts(list: readonly Chip[], start: Date, totalMin: number): TrackPart[] {
  if (!list.length || totalMin <= 0) return [];
  const mins = list.map((c) => Math.max(0, Math.min(totalMin, (Date.parse(c.eta) - start.getTime()) / 60000)));
  return list.map((c, i) => {
    const from = i === 0 ? 0 : (mins[i - 1] + mins[i]) / 2;
    const to = i === list.length - 1 ? totalMin : (mins[i] + mins[i + 1]) / 2;
    return { from, to, color: TRACK_COLOR[c.kind] ?? TRACK_COLOR.cloud, warn: c.warn, rough: from >= ROUGH_AFTER_MIN };
  });
}

/** The slider is for a parked or stopped driver only (FMCSA 49 CFR 392.80/392.82: no dragging on the move; the
 *  driver asks by voice instead), in the weather view, with a route set and the route card up. */
export function sliderVisible(o: { mapView: string; hasRoute: boolean; driving: boolean; phase: string }): boolean {
  return o.mapView === "weather" && o.hasRoute && !o.driving && o.phase === "navigating";
}
