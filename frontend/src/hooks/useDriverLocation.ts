import { useEffect, useSyncExternalStore } from "react";

import { enableLocation, getLocation, startLocation, subscribeLocation } from "../geo/location";
import { useSim } from "../sim";

/** Live GPS with Sacramento fallback when permission is denied or no fix arrives (src/geo/location.ts starts
 *  watching at consent, so the fix is usually in before this mounts). A tester's simulated spot (src/sim.ts)
 *  overrides it. */
export function useDriverLocation() {
  const state = useSyncExternalStore(subscribeLocation, getLocation, getLocation);
  useEffect(() => {
    startLocation(); // no-op when the consent screen or the layout already started it
  }, []);
  const sim = useSim();
  if (sim) {
    return {
      coords: sim.coords,
      heading: sim.heading,
      speed: sim.speed ?? null,
      accuracy: sim.accuracy ?? 5,
      ts: sim.ts ?? 0, // setSim() stamps it; 0 only for a hand-built SimLocation
      isDemo: false,
      resolved: true,
      sim,
      enable: enableLocation,
    };
  }
  return { ...state, sim: null, enable: enableLocation };
}
