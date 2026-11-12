import { useEffect, type RefObject } from "react";

import { api } from "../api";
import { newSessionId, type CopilotTurnController } from "../dialog/useCopilotTurn";
import { chainControlAhead, routePointsAhead } from "../geo/ahead";
import { useNudgesEnabled, useSettings } from "../state/settingsStore";
import { useRouteWeatherStore } from "../state/routeWeatherStore";
import { chips, weatherAhead } from "../weather/routeWeather";
import { useTripStore } from "../state/tripStore";
import type { LatLng } from "../types";
import { PRIORITY } from "../voice/queue";
import type { Guidance } from "./useGuidance";
import { isOnline } from "./useOnline";
import type { SayOptions } from "./useSpeechQueue";

const trip = useTripStore.getState;

// Nudges (/api/nudges): first ask a minute into the drive, then every five minutes; a nudge that
// is still waiting when the next one comes is stale. After one plays the mic opens for ~7 s.
const NUDGE_FIRST_MS = 60 * 1000;
const NUDGE_EVERY_MS = 5 * 60 * 1000;
export const NUDGE_KEY_PREFIX = "nudge-";

/** Moved verbatim from app/index.tsx. `hasRoute` is `!!active`: the route itself must not restart the timer. */
export function useNudges(
  driving: boolean,
  hasRoute: boolean,
  say: (text: string, opts: SayOptions) => void,
  turn: CopilotTurnController,
  guidanceRef: RefObject<Guidance>,
  coordsRef: RefObject<LatLng>,
) {
  // ---- Nudges: while driving, every few minutes the backend may have something to offer (a
  // break coming due, parking ahead). Each line is spoken as ambient; the backend keeps the offer
  // pending for the next turn of the same session, so a tap and "yes" is enough (walkie-talkie: the
  // mic does not open by itself, turnMachine.ts). Not offline, not before "Go". ----
  const nudgesOn = useNudgesEnabled();
  useEffect(() => {
    if (!driving || !hasRoute || !nudgesOn) return;
    const ctrl = new AbortController();
    let every: ReturnType<typeof setInterval> | null = null;
    const poll = async () => {
      if (!isOnline()) return;
      const cur = trip().active;
      if (!cur) return;
      const g = guidanceRef.current;
      // The offer must land in the session the answer will use: a live one, else one started here.
      const before = turn.dialogId();
      const sessionId = turn.isStale() || !before ? newSessionId() : before;
      const since = trip().drivingSince;
      try {
        const { nudges } = await api.nudges(
          {
            lat: coordsRef.current.lat,
            lng: coordsRef.current.lng,
            driving: true,
            active_place: cur.place,
            ahead: routePointsAhead(cur.route.geometry, g.cum, g.matchedIndex, g.alongM),
            chain_control_ahead: chainControlAhead(cur.route.chain_controls, g.cum, g.matchedIndex, g.alongM) ?? undefined,
            weather_ahead: weatherAhead(chips(useRouteWeatherStore.getState().points, cur.route), cur.route, g.cum, g.matchedIndex, g.alongM) ?? undefined,
            drive_elapsed_min: since === null ? null : Math.min(24 * 60, Math.max(0, Math.round((Date.now() - since) / 60000))),
            local_hour: new Date().getHours(),
            session_id: sessionId,
          },
          ctrl.signal,
        );
        if (ctrl.signal.aborted || !nudges.length) return;
        turn.adoptSession(sessionId); // the answer must not start a fresh session
        // The copilot speaks up about the weather ahead: the map shows it too (Figma V1). A plain map switches to the
        // weather view; satellite stays (the driver chose it).
        if (nudges.some((n) => n.kind === "weather") && useSettings.getState().mapView === "map") useRouteWeatherStore.getState().setAutoWeather(true);
        for (const n of nudges) {
          say(n.text, { priority: PRIORITY.WEATHER, kind: "ambient", key: `${NUDGE_KEY_PREFIX}${n.kind}`, ttlMs: NUDGE_EVERY_MS });
        }
      } catch (e) {
        if (!ctrl.signal.aborted) console.log(`[nudges] ${String(e)}`);
      }
    };
    const first = setTimeout(() => {
      poll();
      every = setInterval(poll, NUDGE_EVERY_MS);
    }, NUDGE_FIRST_MS);
    return () => {
      clearTimeout(first);
      if (every) clearInterval(every);
      ctrl.abort();
    };
  }, [driving, hasRoute, nudgesOn, say, turn, guidanceRef, coordsRef]); // refs and a stable callback; `active` itself must not restart the timer
}
