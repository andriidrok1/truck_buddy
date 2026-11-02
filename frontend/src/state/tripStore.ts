// The trip: the one place that owns the active route, the driving flag, the stops, the options and
// providers on the sheet, the parking results and what the sheet shows. The map screen used to hold
// all of this in React state mirrored into refs (`activeRef`, `optionsRef`, `providersRef`,
// `drivingRef`, `backRefs.state`) so async handlers could read it after an await; here any code reads
// `useTripStore.getState()` at the moment it needs the value, and components subscribe to slices.
//
// Persistence: every change of `active`/`driving` is written through src/state/routeStore.ts (validated
// decode, freshness, serialised writes); `restoreSavedTrip()` brings a fresh document back as a preview with
// the restored banner (the screen decides what "Resume" does). zustand's `persist` middleware is not used:
// its envelope would replace the document format the tester and the migration know, and the restore is
// not a plain hydration (a banner, a fit, a trip event).

import { create } from "zustand";

import type { ActiveRoute, ParkingCheck, ParkingState, Place, ProvidersResult, RouteInfo } from "../types";
import { swapAlternative } from "../geo/routeChoice";
import { clearSavedRoute, loadActiveRoute, saveActiveRoute, type SavedRoute } from "./routeStore";

/** What the copilot sheet shows when no dialog turn is running. */
export type Sheet = "ready" | "options" | "providers" | "navigating";

export type TripState = {
  active: ActiveRoute | null;
  driving: boolean;
  /** The route came back from disk and the banner is up (null = no banner). */
  restoredId: string | null;
  /** The saved trip was in driving mode when the app died (the banner's "Resume" is the right default). */
  restoredDriving: boolean;
  /** When the current route was started (break reminder), epoch ms. */
  routeStart: number | null;
  /** When "Go" was pressed, epoch ms; null when not driving. */
  drivingSince: number | null;
  /** Other candidates on the sheet (closest first), and the breakdown providers list. */
  options: Place[];
  providers: ProvidersResult | null;
  /** The copilot's own pin list for the map (the chosen place plus the others). */
  agentPlaces: Place[];
  /** Truck stops around the driver on an empty map. */
  startPins: Place[];
  parking: Record<string, ParkingState>;
  sheet: Sheet;
  /** Bumped on every route replacement; a re-plan that started under an older value is dropped. */
  routeGen: number;
};

export type TripActions = {
  /** A new destination: mints the trip id, bumps the generation, the sheet shows the route. Returns the stored trip. */
  startRoute: (next: ActiveRoute, now?: number) => ActiveRoute;
  /** Same destination, a new line (reroute, re-plan, copilot's stop change): only when `gen` is still current. */
  applyRoute: (gen: number, route: RouteInfo, stops?: Place[]) => boolean;
  /** The sheet's alternative tapped: swaps the primary, keeps the old one in the list. */
  pickAlternative: (i: number) => RouteInfo | null;
  setStops: (stops: Place[]) => void;
  setActiveParking: (placeId: string, parking: ParkingCheck) => void;
  setDriving: (driving: boolean, now?: number) => void;
  /** Everything about the route goes; the providers list (a breakdown) stays on the sheet. */
  endRoute: () => ActiveRoute | null;
  /** A saved trip from disk: preview with the banner, driving off. */
  restore: (saved: SavedRoute, now?: number) => ActiveRoute;
  dismissRestored: () => void;
  setOptions: (options: Place[] | ((prev: Place[]) => Place[])) => void;
  setProviders: (providers: ProvidersResult | null) => void;
  setAgentPlaces: (places: Place[]) => void;
  setStartPins: (pins: Place[]) => void;
  setParking: (parking: Record<string, ParkingState> | ((prev: Record<string, ParkingState>) => Record<string, ParkingState>)) => void;
  setSheet: (sheet: Sheet) => void;
  /** The sheet for the current state: route card, providers, options, or nothing. */
  idleSheet: () => Sheet;
  /** The map's pins: a breakdown's providers, else the copilot's list, else the start pins. */
  places: () => Place[];
  /** Tests only. */
  reset: () => void;
};

export const newTripId = () => `trip-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`;

/** `route.via` as the engine wants it, from the stops (the stops are the source of truth). */
export const viaOf = (stops: readonly Place[] | undefined): [number, number][] => (stops ?? []).map((s) => [s.lat, s.lng]);

const INITIAL: TripState = {
  active: null, driving: false, restoredId: null, restoredDriving: false, routeStart: null, drivingSince: null,
  options: [], providers: null, agentPlaces: [], startPins: [], parking: {}, sheet: "ready", routeGen: 0,
};

export const useTripStore = create<TripState & TripActions>()((set, get) => ({
  ...INITIAL,
  startRoute: (next, now = Date.now()) => {
    const cur = get();
    const tripped: ActiveRoute = { ...next, trip_id: next.trip_id ?? newTripId() };
    set({ active: tripped, sheet: "navigating", routeGen: cur.routeGen + 1, routeStart: cur.active ? cur.routeStart : now, restoredId: null });
    return tripped;
  },
  applyRoute: (gen, route, stops) => {
    const cur = get();
    if (!cur.active || cur.routeGen !== gen) return false;
    set({ active: { ...cur.active, route, ...(stops ? { stops } : {}) }, routeGen: gen + 1 });
    return true;
  },
  pickAlternative: (i) => {
    const cur = get();
    if (!cur.active) return null;
    const { route } = swapAlternative(cur.active.route, i);
    if (route === cur.active.route) return null;
    set({ active: { ...cur.active, route }, routeGen: cur.routeGen + 1 });
    return route;
  },
  setStops: (stops) => {
    const cur = get();
    if (cur.active) set({ active: { ...cur.active, stops } });
  },
  setActiveParking: (placeId, parking) => {
    const cur = get();
    if (cur.active && cur.active.place.id === placeId) set({ active: { ...cur.active, parking } });
  },
  setDriving: (driving, now = Date.now()) => set({ driving, drivingSince: driving ? now : null }),
  endRoute: () => {
    const cur = get();
    set({ active: null, driving: false, drivingSince: null, routeStart: null, restoredId: null, restoredDriving: false, options: [],
      parking: {}, agentPlaces: [], sheet: cur.providers ? "providers" : "ready", routeGen: cur.routeGen + 1 });
    return cur.active;
  },
  restore: (saved, now = Date.now()) => {
    const restored: ActiveRoute = saved.active.trip_id ? saved.active : { ...saved.active, trip_id: newTripId() };
    set((s) => ({ active: restored, driving: false, drivingSince: null, routeStart: now, restoredId: restored.place.id,
      restoredDriving: saved.driving === true, sheet: "navigating", routeGen: s.routeGen + 1 }));
    return restored;
  },
  dismissRestored: () => set({ restoredId: null }),
  setOptions: (options) => set((s) => ({ options: typeof options === "function" ? options(s.options) : options })),
  setProviders: (providers) => set({ providers }),
  setAgentPlaces: (places) => set({ agentPlaces: places }),
  setStartPins: (pins) => set({ startPins: pins }),
  setParking: (parking) => set((s) => ({ parking: typeof parking === "function" ? parking(s.parking) : parking })),
  setSheet: (sheet) => set({ sheet }),
  idleSheet: () => {
    const s = get();
    return s.active ? "navigating" : s.providers ? "providers" : s.options.length ? "options" : "ready";
  },
  places: () => {
    const s = get();
    if (s.providers) return s.providers.providers.map(providerPlace);
    if (s.agentPlaces.length) return s.agentPlaces;
    return s.startPins;
  },
  reset: () => set({ ...INITIAL }),
}));

/** A breakdown provider as a map pin / destination. */
export function providerPlace(p: ProvidersResult["providers"][number]): Place {
  return { id: p.id, name: p.name, category: "service", lat: p.lat, lng: p.lng, address: p.address ?? undefined, phone: p.phone ?? undefined, tags: [] } as Place;
}

/** Selector: the pins on the map, recomputed when any source changes. */
export const selectPlaces = (s: TripState): Place[] =>
  s.providers ? s.providers.providers.map(providerPlace) : s.agentPlaces.length ? s.agentPlaces : s.startPins;

// ---- persistence (routeStore.ts is the adapter) ---------------------------------------------------------

let persisting = false;
let skipSave: ActiveRoute | null = null;

/** Writes every change of the route (and the driving flag) to disk; the object just restored is not written
 *  again, so its saved time stays the original. Idempotent. */
export function startTripPersistence(): () => void {
  if (persisting) return () => {};
  persisting = true;
  const unsub = useTripStore.subscribe((s, prev) => {
    if (s.active === prev.active && s.driving === prev.driving) return;
    if (!s.active) {
      if (prev.active) clearSavedRoute();
      return;
    }
    if (s.active === skipSave && s.driving === prev.driving) return;
    saveActiveRoute(s.active, Date.now(), s.driving);
  });
  return () => {
    persisting = false;
    unsub();
  };
}

/** The saved trip, if fresh, back into the store as a preview with the banner. Returns it, or null. */
export async function restoreSavedTrip(now = Date.now()): Promise<{ restored: ActiveRoute; saved: SavedRoute } | null> {
  const saved = await loadActiveRoute(now);
  if (!saved || useTripStore.getState().active) return null;
  const restored = useTripStore.getState().restore(saved, now);
  skipSave = restored;
  return { restored, saved };
}
