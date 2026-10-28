import { useEffect, useMemo, useRef } from "react";

import { announcement, createRouteMatcher, limitAt, markSpoken, nextManeuver, stageFor } from "../geo/guidance";
import { createOffRouteTracker, thresholdM } from "../geo/offroute";
import type { ActiveRoute, LatLng, Maneuver } from "../types";

export { formatDistance } from "../geo/guidance";

/** One GPS fix: position plus what the off-route tracker needs. */
export type DriverFix = LatLng & {
  /** m/s; null when the platform does not report it. */
  speed?: number | null;
  /** Horizontal accuracy radius, metres; null when unknown. */
  accuracy?: number | null;
  /** Fix time, epoch ms; 0 or missing = no real fix yet (the demo fallback), never tracked. */
  ts?: number;
};

export type Guidance = {
  next: Maneuver | null;
  toNextM: number;
  remainingM: number;
  remainingS: number;
  /** Route segment the truck is on (vertex index of its start); -1 without a route. */
  matchedIndex: number;
  /** Metres travelled along the route to the projected position. */
  alongM: number;
  /** Metres from the truck to the route (off-route signal). */
  distM: number;
  /** cum[i] = metres from the route start to vertex i (see cumulativeMeters). */
  cum: readonly number[];
  /** Posted limit on the current segment, null when the route has none here. */
  limitMph: number | null;
  /** Off-route distance in use for the current fix. */
  thresholdM: number;
  /** True on the tick the tracker decided the truck has left the route; false again on the next fix. */
  offRoute: boolean;
  /** Forget the off-route count and cooldown (a reroute just landed). */
  resetOffRoute: () => void;
};

const NO_CUM: readonly number[] = [0];
const noop = () => {};
const EMPTY: Guidance = {
  next: null, toNextM: 0, remainingM: 0, remainingS: 0, matchedIndex: -1, alongM: 0, distM: 0,
  cum: NO_CUM, limitMph: null, thresholdM: 30, offRoute: false, resetOffRoute: noop,
};

/** Turn-by-turn state from the driver position along the route; speaks maneuvers when `voiceOn`. */
export function useGuidance(
  active: ActiveRoute | null,
  driver: DriverFix,
  driving: boolean,
  voiceOn: boolean,
  say: (text: string) => void,
  /** Called once per off-route verdict with the guidance state of that fix (the map screen reroutes). */
  onOffRoute?: (g: Guidance) => void,
): Guidance {
  // Cumulative distances once per route, plus the last matched segment so each fix only searches
  // around it. A new geometry object gets a fresh matcher, so the hint resets with the route.
  const matcher = useMemo(() => createRouteMatcher(active?.route.geometry ?? []), [active?.route.geometry]);
  // The off-route tracker lives and dies with the route too: a reroute hands back a new geometry
  // (and handle), and its count must not carry over.
  const handle = active?.route.handle;
  // eslint-disable-next-line react-hooks/exhaustive-deps -- the deps are the reset trigger, not inputs
  const tracker = useMemo(() => createOffRouteTracker(), [active?.route.geometry, handle]);

  const state = useMemo<Guidance>(() => {
    const route = active?.route;
    if (!route || !route.geometry.length) return EMPTY;
    const { cum } = matcher;
    const m = matcher.match(driver);
    const total = cum[cum.length - 1] || 1;
    const remainingM = Math.max(0, total - m.alongM);
    const next = nextManeuver(route, m.index);
    const toNextM = next ? Math.max(0, cum[Math.min(next.index, cum.length - 1)] - m.alongM) : remainingM;
    const limitMph = limitAt(route.speed_limits, m.index);
    const speed = driver.speed ?? null;
    const threshold = thresholdM(limitMph, speed);
    // ts 0 = the demo fallback, never a real fix. The tracker replays its verdict for a fix it has
    // already seen, so a memo re-run on the same fix does not count it twice.
    const ts = driver.ts ?? 0;
    const offRoute = ts > 0 && tracker.push({ distM: m.distM, speed, accuracy: driver.accuracy ?? null, ts, thresholdM: threshold }) === "off";
    return {
      next, toNextM, remainingM, remainingS: (route.duration_s * remainingM) / total,
      matchedIndex: m.index, alongM: m.alongM, distM: m.distM, cum,
      limitMph, thresholdM: threshold, offRoute, resetOffRoute: tracker.reset,
    };
  }, [active?.route, driver, matcher, tracker]);

  const spoken = useRef(new Set<string>());
  useEffect(() => {
    spoken.current.clear();
  }, [active?.route]);

  useEffect(() => {
    const { next, toNextM } = state;
    if (!driving || !voiceOn || !next) return;
    const stage = stageFor(toNextM, next.index, spoken.current);
    if (!stage) return;
    markSpoken(spoken.current, next.index, stage);
    say(announcement(next, stage, toNextM));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state, driving, voiceOn]);

  // One call per verdict: `state` only changes with the fix, and the verdict is true for one fix.
  useEffect(() => {
    if (state.offRoute) onOffRoute?.(state);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.offRoute, state]);

  return state;
}
