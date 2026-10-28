import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { BACK_ON_ROUTE_GAP_MS, backOnRoute, createNetAnnouncer, NET_BACK_LINE, NET_HOLD_MS, NET_LOST_LINE } from "../geo/offlineGuidance";
import type { ActiveRoute, LatLng, RouteInfo } from "../types";
import { PRIORITY } from "../voice/queue";
import type { Guidance } from "./useGuidance";
import type { SayOptions } from "./useSpeechQueue";

export type OfflineGuidance = {
  /** The signal is gone (NetInfo, or a failed API call in the last 20 s). */
  offline: boolean;
  /** Off the route with no signal: "Return to the route: 0.4 mi NE" for the banner, else null. */
  backLine: string | null;
  /** The map screen's reroute handler calls this instead of /api/reroute while offline. */
  onOffRoute: (g: Guidance) => void;
};

/**
 * Lost-signal behaviour on an active route: "Lost signal. I'll keep you on the loaded route."
 * once the link has been down for 5 s, "Signal's back." once it has been up again for 5 s; and
 * while off the route with no signal, where the loaded line is (spoken at most once a minute,
 * on the banner all the time). When the signal returns with the truck still off the route,
 * `onBackOnline` runs the normal reroute.
 */
export function useOfflineGuidance(
  active: ActiveRoute | null,
  online: boolean | null,
  driving: boolean,
  guidance: Guidance,
  driver: LatLng & { heading: number | null },
  say: (text: string, opts: SayOptions) => void,
  onBackOnline: (g: Guidance) => void,
): OfflineGuidance {
  const offline = online === false;
  const route = active?.route ?? null;
  const sayRef = useRef(say);
  const backRef = useRef(onBackOnline);
  const guidanceRef = useRef(guidance);
  useEffect(() => {
    sayRef.current = say;
    backRef.current = onBackOnline;
    guidanceRef.current = guidance;
  });

  // Announcer: tick on each change and again after the hold, so a 5 s flap says nothing.
  const announcer = useMemo(() => createNetAnnouncer(), []);
  useEffect(() => {
    if (!active) {
      announcer.reset();
      return;
    }
    const speak = () => {
      const a = announcer.tick(online, Date.now());
      if (a === "lost") sayRef.current(NET_LOST_LINE, { priority: PRIORITY.MANEUVER, kind: "system", key: "net-lost", ttlMs: 30000 });
      if (a === "back") sayRef.current(NET_BACK_LINE, { priority: PRIORITY.WEATHER, kind: "ambient", key: "net-back", ttlMs: 30000 });
    };
    speak();
    const t = setTimeout(speak, NET_HOLD_MS + 50);
    return () => clearTimeout(t);
  }, [active, online, announcer]);

  // Off the route with no signal: `lostFor` is the route the verdict was for; it stops mattering
  // once the truck is back on the line, online again, not driving, or on a new route object
  // (a reroute that landed). Derived, so nothing needs resetting.
  const [lostFor, setLostFor] = useState<RouteInfo | null>(null);
  const lastSpokenAt = useRef(0);
  const onOffRoute = useCallback(
    (g: Guidance) => {
      if (!route) return;
      const b = backOnRoute(route.geometry, g.cum, g.matchedIndex, driver, driver.heading);
      if (!b) return;
      setLostFor(route);
      const now = Date.now();
      if (now - lastSpokenAt.current < BACK_ON_ROUTE_GAP_MS) return;
      lastSpokenAt.current = now;
      sayRef.current(b.text, { priority: PRIORITY.MANEUVER, kind: "system", key: "back-on-route", ttlMs: 30000 });
    },
    [route, driver],
  );
  const onRoute = guidance.matchedIndex >= 0 && guidance.distM <= guidance.thresholdM;
  const lost = lostFor !== null && lostFor === route && driving && !onRoute;
  const lostRef = useRef(lost);
  useEffect(() => {
    lostRef.current = lost;
  }, [lost]);
  // Signal back with the truck still off the route: the normal reroute, right away.
  useEffect(() => {
    if (online === true && lostRef.current) backRef.current(guidanceRef.current);
  }, [online]);

  const backLine = useMemo(() => {
    if (!lost || !offline || !route) return null;
    return backOnRoute(route.geometry, guidance.cum, guidance.matchedIndex, driver, driver.heading)?.line ?? null;
  }, [lost, offline, route, guidance.cum, guidance.matchedIndex, driver]);

  return { offline, backLine, onOffRoute };
}
