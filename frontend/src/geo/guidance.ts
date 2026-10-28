// Pure turn-by-turn maths, extracted from hooks/useGuidance.ts so it can be unit-tested
// (and so off-route detection can build on the same route match). No React in here.

import type { LatLng, Maneuver, RouteInfo } from "../types";

const R = 6371000;
const RAD = Math.PI / 180;

/** Great-circle distance in metres. */
export function haversineM(a: LatLng, b: LatLng): number {
  const dLat = (b.lat - a.lat) * RAD;
  const dLng = (b.lng - a.lng) * RAD;
  const s = Math.sin(dLat / 2) ** 2 + Math.cos(a.lat * RAD) * Math.cos(b.lat * RAD) * Math.sin(dLng / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(s));
}

/** cum[i] = metres along the polyline from its start to vertex i (cum[0] = 0). */
export function cumulativeMeters(geometry: readonly (readonly [number, number])[]): number[] {
  const out = [0];
  for (let i = 1; i < geometry.length; i++) {
    out.push(out[i - 1] + haversineM({ lat: geometry[i - 1][0], lng: geometry[i - 1][1] }, { lat: geometry[i][0], lng: geometry[i][1] }));
  }
  return out;
}

export type RouteMatch = {
  /** Segment the point projects onto: between vertex `index` and `index + 1`. */
  index: number;
  /** Position within that segment, 0 = at vertex `index`, 1 = at vertex `index + 1`. */
  t: number;
  /** Perpendicular distance from the point to the route, metres. */
  distM: number;
  /** Metres along the route from its start to the projected point. */
  alongM: number;
};

/** A windowed match further off than this is suspect (wrong loop of a cloverleaf, GPS jump): rescan everything. */
export const FULL_SCAN_ABOVE_M = 500;

/**
 * Nearest point on the polyline (point-to-segment, equirectangular plane around `point`, fine at
 * route scales). With a `hint` (last matched segment) only segments within `window` of it are
 * tried; the whole route is scanned when there is no hint or the windowed match is further than
 * FULL_SCAN_ABOVE_M. Ties keep the earlier segment.
 */
export function projectOnRoute(
  geometry: readonly (readonly [number, number])[],
  cum: readonly number[],
  point: LatLng,
  hint: number | null,
  window = 50,
): RouteMatch {
  const n = geometry.length;
  if (n === 0) return { index: 0, t: 0, distM: Infinity, alongM: 0 };
  const kx = Math.cos(point.lat * RAD) * R * RAD; // metres per degree of longitude here
  const ky = R * RAD; // metres per degree of latitude
  // Local plane with the driver at the origin: x east, y north, metres.
  const xs = new Float64Array(n);
  const ys = new Float64Array(n);
  for (let i = 0; i < n; i++) {
    xs[i] = (geometry[i][1] - point.lng) * kx;
    ys[i] = (geometry[i][0] - point.lat) * ky;
  }
  if (n === 1) return { index: 0, t: 0, distM: Math.hypot(xs[0], ys[0]), alongM: 0 };

  const scan = (from: number, to: number): RouteMatch => {
    let best: RouteMatch = { index: from, t: 0, distM: Infinity, alongM: cum[from] ?? 0 };
    for (let i = from; i <= to; i++) {
      const ax = xs[i];
      const ay = ys[i];
      const dx = xs[i + 1] - ax;
      const dy = ys[i + 1] - ay;
      const len2 = dx * dx + dy * dy;
      // Projection of the origin onto the segment, clamped to it.
      const t = len2 === 0 ? 0 : Math.min(1, Math.max(0, -(ax * dx + ay * dy) / len2));
      const d = Math.hypot(ax + t * dx, ay + t * dy);
      if (d < best.distM) best = { index: i, t, distM: d, alongM: cum[i] + t * (cum[i + 1] - cum[i]) };
    }
    return best;
  };

  const last = n - 2; // last segment index
  if (hint != null) {
    const windowed = scan(Math.max(0, Math.min(last, hint - window)), Math.min(last, Math.max(0, hint + window)));
    if (windowed.distM <= FULL_SCAN_ABOVE_M) return windowed;
  }
  return scan(0, last);
}

export type RouteMatcher = {
  cum: number[];
  /** Last matched segment, the next match() searches around it; null until the first match. */
  readonly hint: number | null;
  match(point: LatLng): RouteMatch;
};

/** projectOnRoute with its own hint memory; make one per route geometry. */
export function createRouteMatcher(geometry: readonly (readonly [number, number])[], window = 50): RouteMatcher {
  const cum = cumulativeMeters(geometry);
  let hint: number | null = null;
  return {
    cum,
    get hint() {
      return hint;
    },
    match(point) {
      const m = projectOnRoute(geometry, cum, point, hint, window);
      hint = geometry.length ? m.index : null;
      return m;
    },
  };
}

/** First maneuver still ahead of the matched segment (a maneuver at the segment's start vertex is behind). */
export function nextManeuver(route: Pick<RouteInfo, "maneuvers">, matchedIndex: number): Maneuver | null {
  return (route.maneuvers ?? []).find((m) => m.index > matchedIndex) ?? null;
}

export type StageKey = "far" | "near" | "now";

// Announce once ~1 mi out, once ~0.5 mi / 500 ft out, then right at the turn.
export const STAGES: { key: StageKey; max: number; min: number }[] = [
  { key: "far", max: 1800, min: 1000 },
  { key: "near", max: 900, min: 120 },
  { key: "now", max: 90, min: 0 },
];

export const spokenKey = (maneuverIndex: number, stage: StageKey) => `${maneuverIndex}-${stage}`;

/**
 * Stage to announce for the maneuver at `maneuverIndex` when it is `toNextM` away, or null when the
 * distance is between stages or that stage was already spoken. Does not mark anything: call
 * markSpoken() once the announcement goes out.
 */
export function stageFor(toNextM: number, maneuverIndex: number, spoken: ReadonlySet<string>): StageKey | null {
  const stage = STAGES.find((s) => toNextM <= s.max && toNextM >= s.min);
  if (!stage) return null;
  return spoken.has(spokenKey(maneuverIndex, stage.key)) ? null : stage.key;
}

/** Saying the far/near call also counts for the stages above it (a late far call must not be followed by a far call). */
export function markSpoken(spoken: Set<string>, maneuverIndex: number, stage: StageKey) {
  const upto = STAGES.findIndex((s) => s.key === stage);
  STAGES.slice(0, upto + 1).forEach((s) => spoken.add(spokenKey(maneuverIndex, s.key)));
}

/** Spoken lead-in before the maneuver: "In half a mile", "In 500 feet". */
export function spokenDistance(m: number) {
  if (m > 1200) return "In about a mile";
  if (m > 600) return "In half a mile";
  return "In 500 feet";
}

/** The sentence to say for a stage: arrivals and "now" use the raw instruction, the rest get a lead-in. */
export function announcement(next: Pick<Maneuver, "action" | "instruction">, stage: StageKey, toNextM: number) {
  const text =
    next.action === "arrive" || stage === "now"
      ? next.instruction
      : `${spokenDistance(toNextM)}, ${next.instruction.charAt(0).toLowerCase()}${next.instruction.slice(1)}`;
  return `${text}.`;
}

/** "0.4 mi" / "500 ft" for the banner. */
export function formatDistance(m: number) {
  const ft = m * 3.28084;
  if (ft < 1000) return `${Math.max(50, Math.round(ft / 50) * 50)} ft`;
  const mi = m / 1609.34;
  return mi < 10 ? `${mi.toFixed(1)} mi` : `${Math.round(mi)} mi`;
}
