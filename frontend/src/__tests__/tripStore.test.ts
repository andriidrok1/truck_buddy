import AsyncStorage from "@react-native-async-storage/async-storage";
import { AppState, type AppStateStatus } from "react-native";

import { ACTIVE_ROUTE_KEY, decodeSaved } from "../state/routeStore";
import { restoreSavedTrip, selectPlaces, startTripPersistence, stopsAhead, touchSavedTrip, useTripStore, viaOf } from "../state/tripStore";
import type { ActiveRoute, Place, RouteInfo } from "../types";

const T0 = 1_700_000_000_000;
const route = (summary: string, extra: Partial<RouteInfo> = {}): RouteInfo => ({
  geometry: [
    [38.58, -121.49],
    [38.55, -121.42],
  ],
  distance_m: 6759, duration_s: 492, distance_mi: 4.2, duration_min: 8, summary, source: "here", ...extra,
});
const place = (id: string, name = id): Place => ({ id, name, category: "truck_stop", lat: 38.55, lng: -121.42, tags: [] });
const active: ActiveRoute = { place: place("p1", "Pilot"), route: route("I-5 N", { alternatives: [route("I-80 E", { labels: ["I-80"] })] }) };

const flush = () => new Promise((r) => setTimeout(r, 0));

beforeEach(async () => {
  useTripStore.getState().reset();
  await AsyncStorage.clear();
});

describe("tripStore", () => {
  test("startRoute mints a trip id, bumps the generation and shows the route card", () => {
    const s = useTripStore.getState();
    const t = s.startRoute(active, T0);
    expect(t.trip_id).toMatch(/^trip-/);
    expect(useTripStore.getState()).toMatchObject({ sheet: "navigating", routeGen: 1, routeStart: T0 });
    expect(useTripStore.getState().active?.trip_id).toBe(t.trip_id);
    // a second destination keeps the trip's own id but not the first route's start time
    useTripStore.getState().startRoute({ ...active, trip_id: "trip-x" }, T0 + 1000);
    expect(useTripStore.getState()).toMatchObject({ routeGen: 2, routeStart: T0 });
    expect(useTripStore.getState().active?.trip_id).toBe("trip-x");
  });

  test("applyRoute lands only under the generation it started from", () => {
    const s = useTripStore.getState();
    s.startRoute(active);
    const gen = useTripStore.getState().routeGen;
    expect(s.applyRoute(gen, route("US-50"), [place("s1")])).toBe(true);
    expect(useTripStore.getState().active?.route.summary).toBe("US-50");
    expect(useTripStore.getState().active?.stops?.map((p) => p.id)).toEqual(["s1"]);
    // the stale plan is dropped: the generation moved on
    expect(s.applyRoute(gen, route("stale"))).toBe(false);
    expect(useTripStore.getState().active?.route.summary).toBe("US-50");
    expect(viaOf(useTripStore.getState().active?.stops)).toEqual([[38.55, -121.42]]);
  });

  test("applyRoute takes the entry tip phrased for the new route, and keeps the old one when none came", () => {
    const s = useTripStore.getState();
    s.startRoute({ ...active, parking: { verdict: "likely", label: "x", reason: "x", source: "satellite", entry_tip: "On your right, off El Centro Rd", relative: true } as never });
    let gen = useTripStore.getState().routeGen;
    s.applyRoute(gen, route("to E"), undefined, { verdict: "likely", label: "x", reason: "x", source: "satellite", entry_tip: "On your left, off W El Camino Ave", relative: true } as never);
    expect(useTripStore.getState().active?.parking?.entry_tip).toBe("On your left, off W El Camino Ave");
    gen = useTripStore.getState().routeGen;
    s.applyRoute(gen, route("reroute"), undefined, null);
    expect(useTripStore.getState().active?.parking?.entry_tip).toBe("On your left, off W El Camino Ave");
  });

  test("pickAlternative swaps the primary and keeps the old one in the list", () => {
    const s = useTripStore.getState();
    s.startRoute(active);
    const picked = s.pickAlternative(0);
    expect(picked?.labels).toEqual(["I-80"]);
    expect(useTripStore.getState().active?.route.alternatives?.map((r) => r.summary)).toEqual(["I-5 N"]);
    expect(s.pickAlternative(5)).toBeNull();
  });

  test("endRoute clears the trip but keeps a breakdown's providers on the sheet", () => {
    const s = useTripStore.getState();
    s.startRoute(active);
    s.setDriving(true, T0);
    s.setOptions([place("o1")]);
    s.setParking({ o1: { loading: false } });
    expect(useTripStore.getState().drivingSince).toBe(T0);
    const ended = s.endRoute();
    expect(ended?.place.id).toBe("p1");
    expect(useTripStore.getState()).toMatchObject({ active: null, driving: false, drivingSince: null, options: [], parking: {}, sheet: "ready" });
    s.setProviders({ providers: [{ id: "v1", name: "Shop", lat: 1, lng: 2 } as never] });
    s.startRoute(active);
    s.endRoute();
    expect(useTripStore.getState().sheet).toBe("providers");
  });

  test("places: providers win, then the copilot's list, then the start pins", () => {
    const s = useTripStore.getState();
    s.setStartPins([place("pin")]);
    expect(selectPlaces(useTripStore.getState()).map((p) => p.id)).toEqual(["pin"]);
    s.setAgentPlaces([place("a1"), place("a2")]);
    expect(selectPlaces(useTripStore.getState()).map((p) => p.id)).toEqual(["a1", "a2"]);
    s.setProviders({ providers: [{ id: "v1", name: "Shop", lat: 1, lng: 2 } as never] });
    expect(selectPlaces(useTripStore.getState()).map((p) => p.id)).toEqual(["v1"]);
    expect(s.idleSheet()).toBe("providers");
  });

  test("persistence: the trip and the driving flag go to disk and come back as a preview with the banner", async () => {
    const stop = startTripPersistence();
    const s = useTripStore.getState();
    s.startRoute({ ...active, trip_id: "trip-saved" });
    s.setDriving(true);
    await flush();
    const saved = decodeSaved(await AsyncStorage.getItem(ACTIVE_ROUTE_KEY));
    expect(saved?.active.trip_id).toBe("trip-saved");
    expect(saved?.driving).toBe(true);
    // a restart: the store is empty, the saved trip comes back not driving, with the banner, remembering it was
    useTripStore.getState().reset();
    const r = await restoreSavedTrip();
    expect(r?.restored.trip_id).toBe("trip-saved");
    expect(useTripStore.getState()).toMatchObject({ driving: false, restoredId: "p1", restoredDriving: true, sheet: "navigating" });
    // ending the route removes the disk copy
    useTripStore.getState().endRoute();
    await flush();
    expect(await AsyncStorage.getItem(ACTIVE_ROUTE_KEY)).toBeNull();
    stop();
  });

  test("a restore leaves the disk copy as it was: its time and the driving flag survive a second kill", async () => {
    const stop = startTripPersistence();
    useTripStore.getState().startRoute({ ...active, trip_id: "trip-old" });
    useTripStore.getState().setDriving(true);
    await flush();
    const before = await AsyncStorage.getItem(ACTIVE_ROUTE_KEY);
    stop(); // the app is killed
    useTripStore.getState().reset();
    const restart = startTripPersistence(); // and opened an hour later: the screen subscribes, then restores
    await restoreSavedTrip(Date.now() + 60 * 60 * 1000);
    await flush();
    expect(await AsyncStorage.getItem(ACTIVE_ROUTE_KEY)).toBe(before);
    restart();
  });

  test("a multi-day haul: the trip is still there in the morning, counted from when the app last ran with it", async () => {
    const H = 3600 * 1000;
    const go = Date.now(); // 2 pm
    const stop = startTripPersistence();
    useTripStore.getState().startRoute({ ...active, trip_id: "trip-haul" });
    useTripStore.getState().setDriving(true);
    await flush();
    touchSavedTrip(go + 6 * H); // parked at 8 pm: the app was alive with the trip until then
    await flush();
    stop(); // killed overnight
    useTripStore.getState().reset();
    const r = await restoreSavedTrip(go + 16 * H); // 6 am: 16 h after Go, 10 h after the app last ran
    expect(r?.restored.trip_id).toBe("trip-haul");
    expect(r?.saved.driving).toBe(true);
  });

  test("the background re-stamps a restored trip's time but keeps that it was driving; no trip, no stamp", async () => {
    const H = 3600 * 1000;
    let onAppState: (s: AppStateStatus) => void = () => {};
    const listen = jest.spyOn(AppState, "addEventListener").mockImplementation((_type, handler) => {
      onAppState = handler as (s: AppStateStatus) => void;
      return { remove: () => {} } as ReturnType<typeof AppState.addEventListener>;
    });
    const now = jest.spyOn(Date, "now");
    try {
      const t0 = Date.now();
      const stop = startTripPersistence();
      useTripStore.getState().startRoute({ ...active, trip_id: "trip-old" });
      useTripStore.getState().setDriving(true);
      await flush();
      stop(); // the app is killed
      useTripStore.getState().reset();
      const restart = startTripPersistence();
      // in the background before the restore has run: the copy on disk keeps the time it was saved with
      now.mockReturnValue(t0 + H);
      onAppState("background");
      await flush();
      expect(decodeSaved(await AsyncStorage.getItem(ACTIVE_ROUTE_KEY))?.savedAt).toBeLessThan(t0 + H);
      // restored (the banner is up, not driving), then the app goes to the background again
      await restoreSavedTrip(t0 + H);
      now.mockReturnValue(t0 + 2 * H);
      onAppState("inactive"); // iOS on the way out: only "background" counts
      onAppState("background");
      await flush();
      const saved = decodeSaved(await AsyncStorage.getItem(ACTIVE_ROUTE_KEY));
      expect(saved?.savedAt).toBe(t0 + 2 * H);
      expect(saved?.driving).toBe(true);
      expect(useTripStore.getState().driving).toBe(false);
      restart();
    } finally {
      now.mockRestore();
      listen.mockRestore();
    }
  });

  test("selectPlaces gives the same array for the same providers (a fresh one would loop the screen)", () => {
    useTripStore.getState().setProviders({ providers: [{ id: "a", name: "A", lat: 1, lng: 2 }] } as never);
    const a = selectPlaces(useTripStore.getState());
    const b = selectPlaces(useTripStore.getState());
    expect(a).toBe(b);
    expect(a[0]).toMatchObject({ id: "a", category: "service" });
  });
});

describe("stopsAhead", () => {
  // Three sections along a straight east-bound line: start -> s1 -> s2 -> destination.
  const geometry: [number, number][] = [[38.58, -121.6], [38.58, -121.55], [38.58, -121.5], [38.58, -121.45], [38.58, -121.4]];
  const r = { geometry, section_starts: [0, 1, 3] };
  const stops = [place("s1"), place("s2")];
  test("before the first stop both are ahead", () => {
    expect(stopsAhead(r, stops, { lat: 38.58, lng: -121.58 }).map((p) => p.id)).toEqual(["s1", "s2"]);
  });
  test("past the first stop only the second is", () => {
    expect(stopsAhead(r, stops, { lat: 38.58, lng: -121.52 }).map((p) => p.id)).toEqual(["s2"]);
  });
  test("past both, none", () => {
    expect(stopsAhead(r, stops, { lat: 38.58, lng: -121.42 })).toEqual([]);
  });
  test("a route without sections keeps every stop", () => {
    expect(stopsAhead({ geometry, section_starts: undefined }, stops, { lat: 38.58, lng: -121.42 })).toHaveLength(2);
  });
});

test("setDriving(true) while already driving keeps the start time", () => {
  useTripStore.getState().reset();
  const s = useTripStore.getState();
  s.setDriving(true, 1000);
  s.setDriving(true, 9000); // "start driving" said again three hours in
  expect(useTripStore.getState().drivingSince).toBe(1000);
  s.setDriving(false, 9500);
  s.setDriving(true, 10000);
  expect(useTripStore.getState().drivingSince).toBe(10000);
});
