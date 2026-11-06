import * as Sentry from "@sentry/react-native";
import { useEffect } from "react";

import { tripLog } from "../net/tripEvents";
import { restoreSavedTrip, startTripPersistence, useTripStore } from "../state/tripStore";
import type { ActiveRoute, LatLng } from "../types";

const trip = useTripStore.getState;

/**
 * The trip on disk: persistence, the Sentry trip tag, the route restored after a restart and its banner's
 * 20 s timeout. Moved verbatim from app/index.tsx; returns whether the "restored" banner is up.
 */
export function useTripRestore(
  active: ActiveRoute | null,
  driving: boolean,
  restoredId: string | null,
  setDialogPhase: (p: null) => void,
  setFit: (fit: { key: string; points: LatLng[] }) => void,
): boolean {
  // The disk copy of the trip survives a restart (src/state/tripStore.ts writes every change); every Sentry
  // event of a drive carries its trip id, so a crash can be put next to the trip log.
  useEffect(() => {
    Sentry.setTag("trip_id", active?.trip_id ?? "none");
  }, [active?.trip_id]);
  useEffect(() => startTripPersistence(), []);

  // A route saved before the last restart (crash, dead zone, phone off) comes back silently, not
  // driving, with a banner: Resume drives it, Dismiss forgets it.
  useEffect(() => {
    restoreSavedTrip().then((r) => {
      if (!r) return;
      const { restored: res, saved } = r;
      tripLog.log("route_restored", { place_id: res.place.id, saved_age_s: Math.round((Date.now() - saved.savedAt) / 1000), was_driving: saved.driving === true }, res.trip_id);
      setDialogPhase(null);
      // The whole saved line: the GPS fix is not in yet, and the demo point would span half the country.
      const line = res.route.geometry.filter((_, i) => i % 25 === 0).map(([lat, lng]) => ({ lat, lng }));
      setFit({ key: `restored-${res.place.id}`, points: [...line, res.place] });
    });
  }, [setDialogPhase, setFit]); // both stable (state setters)
  const restored = restoredId !== null && active?.place.id === restoredId && !driving;
  useEffect(() => {
    // Nothing pressed in 20 s: the banner goes, the route stays in preview (same as "Keep"). Not when the truck
    // was driving when the app died: then Resume is what the driver came back for, and it stays until pressed.
    if (!restored || trip().restoredDriving) return;
    const t = setTimeout(() => trip().dismissRestored(), 20000);
    return () => clearTimeout(t);
  }, [restored]);
  return restored;
}
