import { activateKeepAwakeAsync, deactivateKeepAwake } from "expo-keep-awake";
import { useEffect, useRef } from "react";

import { setMidTripCheck } from "../data/profile";
import { arrivals, loadArrived, saveArrived } from "../geo/arrival";
import { startDriveTracking, stopDriveTracking } from "../geo/driveTracking";
import { t } from "../i18n";
import { startPositionHeartbeat, tripLog } from "../net/tripEvents";
import { useTripStore } from "../state/tripStore";
import type { ActiveRoute, LatLng } from "../types";

const trip = useTripStore.getState;

const KEEP_AWAKE_TAG = "driving";

type Fix = { coords: LatLng; speed: number | null; ts: number; isDemo: boolean };

/**
 * What runs while the truck drives: background location and keep-awake, the fleet heartbeat and the arrival
 * events. Moved verbatim from app/index.tsx.
 */
export function useDrivingSession(driving: boolean, active: ActiveRoute | null, loc: Fix) {
  // Driving: fixes keep coming with the screen off (a location task, src/geo/driveTracking.ts), and with the app
  // open the screen stays on. Where the task can't run (Expo Go, refused) the awake screen is all there is.
  // The fallback name goes into "Navigating to …" in the phone's notification shade, so it is translated here.
  const drivingTo = driving ? (active?.place.name.split(",")[0] ?? t("your stop")) : null;
  useEffect(() => {
    if (!drivingTo) {
      stopDriveTracking();
      deactivateKeepAwake(KEEP_AWAKE_TAG).catch(() => {});
      return;
    }
    activateKeepAwakeAsync(KEEP_AWAKE_TAG).catch(() => {});
    startDriveTracking(drivingTo);
  }, [drivingTo]);
  useEffect(() => () => {
    stopDriveTracking();
    deactivateKeepAwake(KEEP_AWAKE_TAG).catch(() => {});
  }, []);

  // A fleet truck's stored sizes are not pulled over a trip's own change (src/data/profile.ts).
  useEffect(() => setMidTripCheck(() => !!trip().active), []);

  // A position for the fleet dashboard every few minutes while driving (lat/lng are stamped by the log).
  const speedRef = useRef<number | null>(null);
  useEffect(() => {
    speedRef.current = loc.speed;
  });
  useEffect(() => {
    if (!driving) return;
    return startPositionHeartbeat(tripLog, () => ({ tripId: trip().active?.trip_id ?? null, speedMps: speedRef.current }));
  }, [driving]);

  // Arrival at a stop or the destination: one trip event each (the server counts visits, the fleet reads the time).
  // The places already reported live on disk with the trip id, so a restart mid-trip doesn't report them again.
  const arrived = useRef<{ trip: string | null; seen: Set<string> | null }>({ trip: null, seen: null });
  useEffect(() => {
    const cur = trip().active;
    if (!cur || !driving || loc.isDemo) return;
    const tripId = cur.trip_id ?? null;
    const fix = { ...loc.coords, speed: loc.speed };
    const check = (seen: Set<string>) => {
      const found = arrivals(fix, cur.stops, cur.place, seen);
      for (const a of found) {
        tripLog.log("arrived", { place_id: a.place.id, kind: a.kind, category: a.place.category, name: a.place.name }, tripId);
      }
      if (found.length) saveArrived(tripId, seen).catch(() => {});
    };
    if (arrived.current.trip === tripId && arrived.current.seen) return check(arrived.current.seen);
    arrived.current = { trip: tripId, seen: null };
    loadArrived(tripId).then((seen) => {
      if (arrived.current.trip !== tripId) return;
      arrived.current.seen = seen;
      check(seen);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loc.ts, driving]);
}
