import * as Location from "expo-location";
import { useCallback, useEffect, useRef, useState } from "react";
import { AppState, Linking } from "react-native";

import { DEMO_LOCATION } from "../constants";
import { useSim } from "../sim";
import type { LatLng } from "../types";

type State = {
  coords: LatLng;
  heading: number | null;
  /** m/s from the GPS fix; null when the platform does not report it. */
  speed: number | null;
  /** Horizontal accuracy radius in metres; null when unknown. */
  accuracy: number | null;
  /** Fix time (epoch ms); 0 while only the demo fallback is shown (no real fix yet). */
  ts: number;
  isDemo: boolean;
  resolved: boolean;
};

function withTimeout<T>(p: Promise<T>, ms: number): Promise<T> {
  return Promise.race([p, new Promise<T>((_, rej) => setTimeout(() => rej(new Error("timeout")), ms))]);
}

/** Live GPS with Sacramento fallback when permission is denied or no fix arrives.
 *  Coming back to the app (e.g. after turning location on in Settings) tries again.
 *  A tester's simulated spot (src/sim.ts) overrides both. */
export function useDriverLocation() {
  const [state, setState] = useState<State>({
    coords: DEMO_LOCATION,
    heading: null,
    speed: null,
    accuracy: null,
    ts: 0,
    isDemo: true,
    resolved: false,
  });
  const sim = useSim();
  const sub = useRef<Location.LocationSubscription | null>(null);
  const starting = useRef(false);
  const unmounted = useRef(false);

  /** Asks for permission (only if the OS still allows asking) and starts watching the position. */
  const start = useCallback(async () => {
    if (sub.current || starting.current || unmounted.current) return;
    starting.current = true;
    const apply = (loc: Location.LocationObject) => {
      if (unmounted.current) return;
      setState({
        coords: { lat: loc.coords.latitude, lng: loc.coords.longitude },
        heading: loc.coords.heading ?? null,
        speed: loc.coords.speed ?? null,
        accuracy: loc.coords.accuracy ?? null,
        ts: loc.timestamp,
        isDemo: false,
        resolved: true,
      });
    };
    try {
      let perm = await withTimeout(Location.getForegroundPermissionsAsync(), 5000);
      if (perm.status !== "granted" && perm.canAskAgain) {
        perm = await Location.requestForegroundPermissionsAsync();
      }
      if (perm.status !== "granted") throw new Error("denied");
      const last = await Location.getLastKnownPositionAsync().catch(() => null);
      if (last) apply(last);
      const watch = await Location.watchPositionAsync({ accuracy: Location.Accuracy.High, distanceInterval: 20 }, apply);
      if (unmounted.current) watch.remove();
      else sub.current = watch;
      // A first fix right away; the watch keeps it fresh. No fix (e.g. indoors) keeps the last known one.
      if (!last) apply(await withTimeout(Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.Balanced }), 10000));
    } catch {
      if (!unmounted.current) setState((s) => (s.isDemo ? { ...s, resolved: true } : s));
    } finally {
      starting.current = false;
    }
  }, []);

  useEffect(() => {
    unmounted.current = false;
    start();
    const appSub = AppState.addEventListener("change", (s) => {
      if (s === "active") start();
    });
    return () => {
      unmounted.current = true;
      appSub.remove();
      sub.current?.remove();
      sub.current = null;
    };
  }, [start]);

  /** "Location off" tag: ask again, or open Settings once the OS won't show the prompt anymore. */
  const enable = useCallback(async () => {
    const perm = await Location.getForegroundPermissionsAsync().catch(() => null);
    if (perm && perm.status !== "granted" && !perm.canAskAgain) {
      Linking.openSettings?.()?.catch(() => {});
      return;
    }
    start();
  }, [start]);

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
      enable,
    };
  }
  return { ...state, sim: null, enable };
}
