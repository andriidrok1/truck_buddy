import { useEffect, useMemo, useRef } from "react";

import { announcement, createRouteMatcher, markSpoken, nextManeuver, stageFor } from "../geo/guidance";
import type { ActiveRoute, LatLng, Maneuver } from "../types";

export { formatDistance } from "../geo/guidance";

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
};

const EMPTY: Guidance = { next: null, toNextM: 0, remainingM: 0, remainingS: 0, matchedIndex: -1, alongM: 0, distM: 0 };

/** Turn-by-turn state from the driver position along the route; speaks maneuvers when `voiceOn`. */
export function useGuidance(
  active: ActiveRoute | null,
  driver: LatLng,
  driving: boolean,
  voiceOn: boolean,
  say: (text: string) => void,
): Guidance {
  // Cumulative distances once per route, plus the last matched segment so each fix only searches
  // around it. A new geometry object gets a fresh matcher, so the hint resets with the route.
  const matcher = useMemo(() => createRouteMatcher(active?.route.geometry ?? []), [active?.route.geometry]);

  const state = useMemo<Guidance>(() => {
    const route = active?.route;
    if (!route || !route.geometry.length) return EMPTY;
    const { cum } = matcher;
    const m = matcher.match(driver);
    const total = cum[cum.length - 1] || 1;
    const remainingM = Math.max(0, total - m.alongM);
    const next = nextManeuver(route, m.index);
    const toNextM = next ? Math.max(0, cum[Math.min(next.index, cum.length - 1)] - m.alongM) : remainingM;
    return { next, toNextM, remainingM, remainingS: (route.duration_s * remainingM) / total, matchedIndex: m.index, alongM: m.alongM, distM: m.distM };
  }, [active?.route, driver, matcher]);

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

  return state;
}
