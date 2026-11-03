// One GPS fix through the whole guidance chain: route match, next maneuver and the distance to it,
// posted limit, off-route verdict, and the maneuver stage that is due. useGuidance runs this per fix
// on the device; replay.ts runs it over a recorded or synthetic trace in tests, so the two can never
// drift apart. Pure: no React, no timers, no speech.

import { limitAt } from "./advisories";
import { createRouteMatcher, nextManeuver, remainingSeconds, stageFor, type RouteMatch, type RouteMatcher, type StageKey } from "./guidance";
import { createOffRouteTracker, thresholdM, type OffRouteTracker, type OffRouteVerdict } from "./offroute";
import type { LatLng, Maneuver, RouteInfo } from "../types";

/** One GPS fix: position plus what the off-route tracker needs. */
export type GuidanceFix = LatLng & {
  /** m/s; null when the platform does not report it. */
  speed?: number | null;
  /** Horizontal accuracy radius, metres; null when unknown. */
  accuracy?: number | null;
  /** Fix time, epoch ms; 0 or missing = no real fix yet (the demo fallback), never tracked. */
  ts?: number;
};

export type GuidanceStep = {
  match: RouteMatch;
  next: Maneuver | null;
  toNextM: number;
  remainingM: number;
  remainingS: number;
  limitMph: number | null;
  thresholdM: number;
  /** The tracker's verdict for this fix; null for a fix without a timestamp (never tracked). */
  verdict: OffRouteVerdict | null;
  /** The maneuver stage due for `next` at this distance and not yet spoken, or null. Not marked here. */
  stage: StageKey | null;
};

export type GuidanceState = { matcher: RouteMatcher; tracker: OffRouteTracker; spoken: Set<string> };

/** Fresh matcher, tracker and spoken set for a route (the hook keys these on the route object). */
export function createGuidanceState(route: Pick<RouteInfo, "geometry">): GuidanceState {
  return { matcher: createRouteMatcher(route.geometry), tracker: createOffRouteTracker(), spoken: new Set() };
}

export function guidanceStep(route: RouteInfo, state: GuidanceState, fix: GuidanceFix): GuidanceStep {
  const { cum } = state.matcher;
  const match = state.matcher.match(fix);
  const total = cum[cum.length - 1] || 1;
  const remainingM = Math.max(0, total - match.alongM);
  const next = nextManeuver(route, match.index);
  const toNextM = next ? Math.max(0, cum[Math.min(next.index, cum.length - 1)] - match.alongM) : remainingM;
  const limitMph = limitAt(route.speed_limits, match.index);
  const speed = fix.speed ?? null;
  const threshold = thresholdM(limitMph, speed);
  // ts 0 = the demo fallback, never a real fix. The tracker replays its verdict for a fix it has
  // already seen, so running the same fix twice does not count it twice.
  const ts = fix.ts ?? 0;
  const verdict = ts > 0 ? state.tracker.push({ distM: match.distM, speed, accuracy: fix.accuracy ?? null, ts, thresholdM: threshold }) : null;
  const stage = next ? stageFor(toNextM, next.index, state.spoken) : null;
  return {
    match, next, toNextM, remainingM, remainingS: remainingSeconds(route, remainingM, total, match.index),
    limitMph, thresholdM: threshold, verdict, stage,
  };
}
