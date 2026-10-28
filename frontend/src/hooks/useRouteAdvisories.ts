import { useEffect, useMemo, useRef } from "react";

import { advisoryText, createSpeedWatch, MPH_PER_MPS, upcomingAdvisories, type Advisory } from "../geo/advisories";
import type { ActiveRoute } from "../types";
import { PRIORITY } from "../voice/queue";
import type { Guidance } from "./useGuidance";
import type { SayOptions } from "./useSpeechQueue";
import { useSpeedAlerts } from "./useTruckProfile";

/** Advisories within this distance are spoken (and shown). */
const SPEAK_WITHIN_M = 16000;
const ADVISORY_TTL_MS = 5 * 60 * 1000;

/**
 * What lies ahead on the active route, spoken once each (per route, per `${kind}:${id}`) once
 * within 16 km: closures, severe weather and R-2/R-3 chains at priority 1 (cut ambient lines,
 * never a maneuver), the rest as ambient. Speed alerts ("You're doing 70 in a 55.") at ambient
 * priority 3, only with the "Speed limit alerts" switch on (off by default: the limits come from
 * map data and a wrong one nagging the driver is worse than none).
 */
export function useRouteAdvisories(
  active: ActiveRoute | null,
  guidance: Guidance,
  speedMps: number | null,
  say: (text: string, opts: SayOptions) => void,
) {
  const speedAlerts = useSpeedAlerts();
  const route = active?.route;
  const advisories = useMemo<Advisory[]>(
    () => (route ? upcomingAdvisories(route, guidance.matchedIndex, guidance.cum) : []),
    [route, guidance.matchedIndex, guidance.cum],
  );

  // Spoken-once set and the speed watch live and die with the route.
  const spoken = useRef(new Set<string>());
  const watch = useRef(createSpeedWatch());
  useEffect(() => {
    spoken.current.clear();
    watch.current.reset();
  }, [route]);

  const sayRef = useRef(say);
  useEffect(() => {
    sayRef.current = say;
  });

  useEffect(() => {
    for (const a of advisories) {
      if (a.aheadM > SPEAK_WITHIN_M) break;
      const k = `${a.kind}:${a.id}`;
      if (spoken.current.has(k)) continue;
      spoken.current.add(k);
      const urgent = a.severity === 3;
      sayRef.current(advisoryText(a), {
        priority: urgent ? PRIORITY.CLOSURE_CHAIN : PRIORITY.WEATHER,
        kind: urgent ? "system" : "ambient",
        key: `adv-${k}`,
        ttlMs: ADVISORY_TTL_MS,
      });
    }
  }, [advisories]);

  const speedMph = speedMps == null ? null : Math.round(speedMps * MPH_PER_MPS);
  const limitMph = guidance.limitMph;
  useEffect(() => {
    if (!route) return;
    const verdict = watch.current.push({ speedMph, limitMph, now: Date.now() });
    if (verdict === "alert" && speedAlerts && speedMph != null && limitMph != null) {
      sayRef.current(`You're doing ${speedMph} in a ${limitMph}.`, { priority: PRIORITY.SPEED, kind: "ambient", key: "speed", ttlMs: 20000 });
    }
    // The watch runs on every fix (guidance changes with the fix); the alert text is for that fix.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [guidance, route, speedAlerts]);

  return { advisories, limitMph, speedMph };
}
