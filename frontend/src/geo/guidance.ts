// Pure turn-by-turn maths, extracted from hooks/useGuidance.ts so it can be unit-tested
// (and so off-route detection can build on the same route match). No React in here.

import { getLang, t, type Lang } from "../i18n";
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

/**
 * The distance the driver sees and hears, rounded once: feet in 50s under 1000 ft, else miles to a tenth
 * (whole miles from 10). The banner read "600 ft" while the voice said "In 500 feet" in the same second: the
 * voice had a fixed phrase per stage (QA 2026-11-02). Banner and voice both read this.
 */
export function roundedDistance(m: number): { n: number; unit: "ft" | "mi" } {
  const ft = m * 3.28084;
  if (ft < 1000) return { n: Math.max(50, Math.round(ft / 50) * 50), unit: "ft" };
  const mi = m / 1609.34;
  return { n: mi < 10 ? Math.round(mi * 10) / 10 : Math.round(mi), unit: "mi" };
}

/** Spoken lead-in before the maneuver: "In 600 feet", "In half a mile", "In 1.2 miles". */
export function spokenDistance(m: number) {
  const { n, unit } = roundedDistance(m);
  if (unit === "ft") return `In ${n} feet`;
  if (n === 0.5) return "In half a mile";
  if (n === 1) return "In a mile";
  return `In ${n} miles`;
}

/** Has a Cyrillic letter: the server translated the instruction (a line its catalog misses stays English whole). */
const CYRILLIC = /[а-яё]/i;

/**
 * The router's action and direction as a short phrase ("turn left", "take the exit on the right"), for when its own
 * sentence can't be used. English here; src/i18n/ru.ts has the Russian side of every phrase this can return.
 */
export function maneuverWords(action: string, direction?: string | null): string {
  const side = direction === "left" || direction === "right" ? direction : null;
  switch (action) {
    case "turn":
      return side ? `turn ${side}` : "follow the route";
    case "keep":
    case "continueHighway":
      return side ? `keep ${side}` : direction === "middle" ? "keep to the middle" : "continue straight";
    case "exit":
      return side ? `take the exit on the ${side}` : "take the exit";
    case "ramp":
    case "enterHighway":
      return side ? `take the ramp on the ${side}` : "take the ramp";
    case "uTurn":
      return "make a U-turn";
    case "roundaboutEnter":
    case "roundaboutPass":
    case "roundaboutExit":
      return "go through the roundabout";
    case "ferry":
      return "take the ferry";
    case "continue":
      return "continue straight";
    default:
      return side ? `turn ${side}` : "follow the route";
  }
}

/**
 * The Russian sentence for the two cases where gluing "In 500 feet, " to the instruction goes wrong, or null when
 * the usual path is right. Decided by the maneuver's action, never by its words (QA 2026-11-11):
 * - an arrival: the server sends "Ты на месте" or "Прибытие: Pilot", and a mile out that read "Через полмили ты
 *   на месте." Before the end it is one whole sentence; at the end the server's own line.
 * - an instruction the server's catalog left in English: "Через 500 футов turn left onto Main St." was read by
 *   the Russian voice. The action and direction are said in Russian instead, without the road name.
 */
function announcementRu(next: Pick<Maneuver, "action" | "instruction" | "direction">, stage: StageKey, toNextM: number): string | null {
  const translated = CYRILLIC.test(next.instruction);
  const lead = () => t(spokenDistance(toNextM), undefined, "ru");
  if (next.action === "arrive") {
    if (stage !== "now") return t("{lead}, you'll arrive.", { lead: lead() }, "ru");
    return translated ? null : t("You have arrived.", undefined, "ru");
  }
  if (translated) return null;
  const what = t(maneuverWords(next.action, next.direction), undefined, "ru");
  return stage === "now" ? `${what.charAt(0).toUpperCase()}${what.slice(1)}.` : `${lead()} ${what}.`;
}

/**
 * The sentence to say for a stage: "now" is the raw instruction; the far and near calls get a lead-in, and an
 * arrival becomes "In half a mile, you'll arrive at…" (the raw "Arrive at X" a mile out sounded like the end).
 * The English frame around a Russian instruction is translated where it is spoken (useSpeechQueue runs it through
 * t()); the Russian cases that frame can't carry are whole sentences from announcementRu.
 */
export function announcement(next: Pick<Maneuver, "action" | "instruction" | "direction">, stage: StageKey, toNextM: number, lang: Lang = getLang()) {
  const ru = lang === "ru" ? announcementRu(next, stage, toNextM) : null;
  if (ru) return ru;
  if (stage === "now") return `${next.instruction}.`;
  const body =
    next.action === "arrive" && /^arrive\b/i.test(next.instruction)
      ? next.instruction.replace(/^arrive\b/i, "you'll arrive")
      : `${next.instruction.charAt(0).toLowerCase()}${next.instruction.slice(1)}`;
  return `${spokenDistance(toNextM)}, ${body}.`;
}

/**
 * Seconds to the destination. The driving time is spread over the remaining distance; the time standing at
 * the stops still ahead (a section start past the matched segment is a stop still to make) is added whole,
 * so a 30-minute stop neither inflates the first miles nor lingers in the ETA once it is behind the truck.
 */
export function remainingSeconds(
  route: Pick<RouteInfo, "duration_s" | "stops_s" | "section_starts">,
  remainingM: number,
  totalM: number,
  matchedIndex: number,
): number {
  const starts = route.section_starts ?? [];
  const stops = Math.max(0, starts.length - 1);
  const stopsS = route.stops_s ?? 0;
  const driving = Math.max(0, route.duration_s - stopsS);
  const ahead = stops ? starts.filter((i, k) => k > 0 && i > matchedIndex).length : 0;
  return (driving * remainingM) / (totalM || 1) + (stops ? (stopsS / stops) * ahead : 0);
}

/** "0.4 mi" / "2 mi" / "500 ft" for the banner (the same rounding the voice reads). A whole mile has no ".0":
 *  Russian showed "1,0 мили" and "5,0 мили" where it should say "1 миля" and "5 миль" (QA 2026-11-11). */
export function formatDistance(m: number) {
  const { n, unit } = roundedDistance(m);
  return `${n} ${unit}`;
}

/** Beyond this from the line, a resumed route is replanned at once: the off-route tracker waits for the
 *  truck to move, and until then the banner read "50 ft, turn right onto Auburn Blvd" 15 km away
 *  (QA 2026-11-02, bug 31). Well above the 50 m highway band, so GPS noise on the line never trips it. */
export const RESUME_REPLAN_M = 150;

export function farFromRoute(geometry: readonly (readonly [number, number])[], point: LatLng, limitM = RESUME_REPLAN_M): boolean {
  if (geometry.length === 0) return false;
  return projectOnRoute(geometry, cumulativeMeters(geometry), point, null).distM > limitM;
}
