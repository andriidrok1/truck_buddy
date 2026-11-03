import { useEffect, useMemo } from "react";

import { announcement, markSpoken, type StageKey } from "../geo/guidance";
import { createGuidanceState, guidanceStep, type GuidanceFix } from "../geo/guidanceStep";
import type { ActiveRoute, Maneuver } from "../types";
import { PRIORITY } from "../voice/queue";
import type { SayOptions } from "./useSpeechQueue";

export { formatDistance } from "../geo/guidance";

/** One GPS fix: position plus what the off-route tracker needs (src/geo/guidanceStep.ts). */
export type DriverFix = GuidanceFix;

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
  say: (text: string, opts: SayOptions) => void,
  /** Called once per off-route verdict with the guidance state of that fix (the map screen reroutes). */
  onOffRoute?: (g: Guidance) => void,
  /** Called as each maneuver stage is handed to the speech queue (trip events: late "now" calls). */
  onAnnounce?: (next: Maneuver, stage: StageKey, toNextM: number) => void,
): Guidance {
  // Matcher (cumulative distances, last matched segment), off-route tracker and the set of spoken
  // stages live and die with the route: a reroute or an alternative hands back a new geometry object
  // (and handle) and none of the three may carry over. One state object per route, shared with the
  // replay used in tests (src/geo/guidanceStep.ts).
  const handle = active?.route.handle;
  // eslint-disable-next-line react-hooks/exhaustive-deps -- the deps are the reset trigger, not inputs
  const gs = useMemo(() => createGuidanceState(active?.route ?? { geometry: [] }), [active?.route.geometry, handle]);

  const state = useMemo<Guidance & { stage: StageKey | null }>(() => {
    const route = active?.route;
    if (!route || !route.geometry.length) return { ...EMPTY, stage: null };
    const step = guidanceStep(route, gs, driver);
    return {
      next: step.next, toNextM: step.toNextM, remainingM: step.remainingM, remainingS: step.remainingS,
      matchedIndex: step.match.index, alongM: step.match.alongM, distM: step.match.distM, cum: gs.matcher.cum,
      limitMph: step.limitMph, thresholdM: step.thresholdM, offRoute: step.verdict === "off", resetOffRoute: gs.tracker.reset,
      stage: step.stage,
    };
  }, [active?.route, driver, gs]);

  useEffect(() => {
    const { next, toNextM, stage } = state;
    if (!driving || !voiceOn || !next || !stage) return;
    markSpoken(gs.spoken, next.index, stage);
    // A due turn beats everything and is stale after 8 s; far/near calls share a key, so an
    // unplayed far call is replaced by the near one instead of playing late.
    const opts: SayOptions =
      stage === "now"
        ? { priority: PRIORITY.MANEUVER_NOW, ttlMs: 8000, key: `m-${next.index}-now`, kind: "maneuver" }
        : { priority: PRIORITY.MANEUVER, ttlMs: 30000, key: `m-${next.index}`, kind: "maneuver" };
    say(announcement(next, stage, toNextM), opts);
    onAnnounce?.(next, stage, toNextM);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state, driving, voiceOn]);

  // One call per verdict: `state` only changes with the fix, and the verdict is true for one fix.
  useEffect(() => {
    if (state.offRoute) onOffRoute?.(state);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.offRoute, state]);

  return state;
}
