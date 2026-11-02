// Replays a trace (recorded or synthetic) through the guidance chain and reports what the driver would
// have heard and when the tracker would have called the truck off the route. The regression test in
// CI runs this over fixtures/traces and fixtures/routes; the numbers it checks are the same ones the
// device computes, because both go through guidanceStep().

import { announcement, markSpoken, type StageKey } from "./guidance";
import { createGuidanceState, guidanceStep } from "./guidanceStep";
import type { TracePoint } from "./trace";
import type { Maneuver, RouteInfo } from "../types";

export type ReplayVerdict = { t_ms: number; verdict: "on" | "off" | "skip"; distM: number; thresholdM: number; alongM: number };
export type ReplayAnnouncement = {
  t_ms: number;
  maneuverIndex: number;
  action: Maneuver["action"];
  stage: StageKey;
  text: string;
  toNextM: number;
  /** Metres along the route at the fix that triggered the call. */
  alongM: number;
};

export type Replay = {
  verdicts: ReplayVerdict[];
  announcements: ReplayAnnouncement[];
  /** t_ms of every "off" verdict. */
  offRouteAt: number[];
  /** Metres along the route of every maneuver vertex (cum[index]). */
  maneuverAlongM: number[];
};

/** The trace clock starts at `startTs` (any positive epoch ms; the tracker only needs gaps). */
export const REPLAY_START_TS = 1_700_000_000_000;

export function replayGuidance(route: RouteInfo, fixes: readonly TracePoint[], startTs = REPLAY_START_TS): Replay {
  const state = createGuidanceState(route);
  const verdicts: ReplayVerdict[] = [];
  const announcements: ReplayAnnouncement[] = [];
  const offRouteAt: number[] = [];
  for (const p of fixes) {
    const step = guidanceStep(route, state, { lat: p.lat, lng: p.lng, speed: p.speed, accuracy: p.accuracy, ts: startTs + p.t_ms });
    if (step.verdict) {
      verdicts.push({ t_ms: p.t_ms, verdict: step.verdict, distM: step.match.distM, thresholdM: step.thresholdM, alongM: step.match.alongM });
      if (step.verdict === "off") offRouteAt.push(p.t_ms);
    }
    if (step.next && step.stage) {
      markSpoken(state.spoken, step.next.index, step.stage);
      announcements.push({
        t_ms: p.t_ms, maneuverIndex: step.next.index, action: step.next.action, stage: step.stage,
        text: announcement(step.next, step.stage, step.toNextM), toNextM: step.toNextM, alongM: step.match.alongM,
      });
    }
  }
  const cum = state.matcher.cum;
  return { verdicts, announcements, offRouteAt, maneuverAlongM: (route.maneuvers ?? []).map((m) => cum[Math.min(m.index, cum.length - 1)]) };
}
