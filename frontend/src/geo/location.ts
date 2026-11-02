// The driver's position as one module-level store: started as soon as consent is given (ConsentGate, the
// root layout), not when the map screen mounts. Onboarding takes 30 s or more, so the first GPS fix is in
// before the map is on screen; before this the map opened on the demo point and sat empty for 7-9 s while
// permissions, the last known position and the first fix were fetched (QA 2026-10-30, H2).
// useDriverLocation subscribes; the tester's simulated spot (src/sim.ts) overrides it there.

import * as Location from "expo-location";
import { AppState, Linking } from "react-native";

import { DEMO_LOCATION } from "../constants";
import type { LatLng } from "../types";

export type DriverLocation = {
  coords: LatLng;
  heading: number | null;
  /** m/s from the GPS fix; null when the platform does not report it. */
  speed: number | null;
  /** Horizontal accuracy radius in metres; null when unknown. */
  accuracy: number | null;
  /** Fix time (epoch ms); 0 while only the demo fallback is shown (no real fix yet). */
  ts: number;
  isDemo: boolean;
  /** The first attempt is over: a real fix, or the demo point for good (denied, no fix). */
  resolved: boolean;
};

const DEMO: DriverLocation = { coords: DEMO_LOCATION, heading: null, speed: null, accuracy: null, ts: 0, isDemo: true, resolved: false };

let state: DriverLocation = DEMO;
const listeners = new Set<() => void>();
let watch: Location.LocationSubscription | null = null;
let starting = false;
let appStateHooked = false;

const set = (next: DriverLocation) => {
  state = next;
  listeners.forEach((l) => l());
};

export const getLocation = (): DriverLocation => state;

export function subscribeLocation(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

function withTimeout<T>(p: Promise<T>, ms: number): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const late = new Promise<T>((_, rej) => {
    timer = setTimeout(() => rej(new Error("timeout")), ms);
  });
  return Promise.race([p, late]).finally(() => clearTimeout(timer));
}

const apply = (loc: Location.LocationObject) =>
  set({
    coords: { lat: loc.coords.latitude, lng: loc.coords.longitude },
    heading: loc.coords.heading ?? null,
    speed: loc.coords.speed ?? null,
    accuracy: loc.coords.accuracy ?? null,
    ts: loc.timestamp,
    isDemo: false,
    resolved: true,
  });

/**
 * Starts watching the position, asking for permission first when `ask` is set and the OS still allows the
 * prompt. Safe to call any number of times: one watch, one start in flight. Coming back to the app (location
 * turned on in Settings) tries again. The consent screen and the root layout pass `ask: false`: the consent
 * screen has just shown the system prompt itself, and a "Deny" there must not be followed by a second prompt
 * on top of onboarding (U1); the map screen and the "location off" tag ask.
 */
export async function startLocation({ ask = true }: { ask?: boolean } = {}): Promise<void> {
  if (watch || starting) return;
  starting = true;
  if (!appStateHooked) {
    appStateHooked = true;
    AppState.addEventListener("change", (s) => {
      if (s === "active") startLocation({ ask: false });
    });
  }
  try {
    let perm = await withTimeout(Location.getForegroundPermissionsAsync(), 5000);
    if (perm.status !== "granted" && perm.canAskAgain && ask) {
      perm = await Location.requestForegroundPermissionsAsync();
    }
    if (perm.status !== "granted") throw new Error("denied");
    const last = await Location.getLastKnownPositionAsync().catch(() => null);
    if (last) apply(last);
    watch = await Location.watchPositionAsync({ accuracy: Location.Accuracy.High, distanceInterval: 20 }, apply);
    // A first fix right away; the watch keeps it fresh. No fix (e.g. indoors) keeps the last known one.
    if (!last) apply(await withTimeout(Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.Balanced }), 10000));
  } catch {
    if (state.isDemo) set({ ...state, resolved: true });
  } finally {
    starting = false;
  }
}

/** "Location off" tag: ask again, or open Settings once the OS won't show the prompt anymore. */
export async function enableLocation(): Promise<void> {
  const perm = await Location.getForegroundPermissionsAsync().catch(() => null);
  if (perm && perm.status !== "granted" && !perm.canAskAgain) {
    Linking.openSettings?.()?.catch(() => {});
    return;
  }
  await startLocation();
}

/** Tests only: back to the demo point, no watch. */
export function resetLocationForTests() {
  watch?.remove();
  watch = null;
  starting = false;
  state = DEMO;
  listeners.clear();
}
