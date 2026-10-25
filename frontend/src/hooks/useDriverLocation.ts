import * as Location from "expo-location";
import { useEffect, useState } from "react";

import { DEMO_LOCATION } from "../constants";
import { useSim } from "../sim";
import type { LatLng } from "../types";

type State = { coords: LatLng; heading: number | null; isDemo: boolean; resolved: boolean };

function withTimeout<T>(p: Promise<T>, ms: number): Promise<T> {
  return Promise.race([p, new Promise<T>((_, rej) => setTimeout(() => rej(new Error("timeout")), ms))]);
}

/** Live GPS with Sacramento fallback when permission is denied or no fix arrives.
 *  A tester's simulated spot (src/sim.ts) overrides both. */
export function useDriverLocation() {
  const [state, setState] = useState<State>({ coords: DEMO_LOCATION, heading: null, isDemo: true, resolved: false });
  const sim = useSim();

  useEffect(() => {
    let cancelled = false;
    let sub: Location.LocationSubscription | null = null;
    const apply = (loc: Location.LocationObject) => {
      if (cancelled) return;
      setState({
        coords: { lat: loc.coords.latitude, lng: loc.coords.longitude },
        heading: loc.coords.heading ?? null,
        isDemo: false,
        resolved: true,
      });
    };

    (async () => {
      try {
        let perm = await withTimeout(Location.getForegroundPermissionsAsync(), 5000);
        if (perm.status !== "granted" && perm.canAskAgain) {
          perm = await Location.requestForegroundPermissionsAsync();
        }
        if (perm.status !== "granted") throw new Error("denied");
        const last = await Location.getLastKnownPositionAsync().catch(() => null);
        if (last) apply(last);
        const cur = await withTimeout(Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.Balanced }), 10000);
        apply(cur);
        sub = await Location.watchPositionAsync({ accuracy: Location.Accuracy.High, distanceInterval: 20 }, apply);
        if (cancelled) sub.remove();
      } catch {
        if (!cancelled) setState((s) => (s.isDemo ? { ...s, resolved: true } : s));
      }
    })();

    return () => {
      cancelled = true;
      sub?.remove();
    };
  }, []);

  if (sim) return { coords: sim.coords, heading: sim.heading, isDemo: false, resolved: true, sim };
  return { ...state, sim: null };
}
