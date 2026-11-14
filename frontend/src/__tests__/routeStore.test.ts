import AsyncStorage from "@react-native-async-storage/async-storage";

import { ACTIVE_ROUTE_KEY, clearSavedRoute, decodeSaved, encodeSaved, getSavedAt, isFresh, loadActiveRoute, MAX_AGE_MS, saveActiveRoute, savedAgeLabel, touchSavedRoute } from "../state/routeStore";
import type { ActiveRoute, RouteInfo } from "../types";

const T0 = 1_700_000_000_000;
const H = 3600 * 1000;

const route = (summary: string, extra: Partial<RouteInfo> = {}): RouteInfo => ({
  geometry: [
    [38.58, -121.49],
    [38.55, -121.42],
  ],
  distance_m: 6759,
  duration_s: 492,
  distance_mi: 4.2,
  duration_min: 8,
  summary,
  source: "here",
  maneuvers: [{ action: "turn", direction: "left", instruction: "Turn left", lat: 38.55, lng: -121.42, index: 1 }],
  speed_limits: [{ index: 0, mph: 65 }],
  incidents: [{ type: "closure", closed: true, description: "Road closed", index: 1, criticality: "critical" }],
  weather: [{ id: "w1", event: "Winter Storm Warning", severity: "Severe", headline: "Snow", area: "Sierra", index: 1 }],
  chain_controls: [{ id: "c1", route: "I-80", location: "Kingvale", status: "R-2", index: 1, lat: 39.3, lng: -120.4 }],
  handle: "h-1",
  ...extra,
});

const active: ActiveRoute = {
  place: { id: "p1", name: "Pilot Sacramento", category: "truck_stop", lat: 38.55, lng: -121.42, tags: ["diesel"] },
  route: route("I-5 N", {
    via: [[38.56, -121.45]],
    section_starts: [0, 1],
    alternatives: [route("I-80 E", { handle: "h-80", tolls_usd: 12.5 })],
  }),
  parking: { verdict: "likely", label: "Likely", reason: "big lot", source: "satellite" },
  stops: [{ id: "s1", name: "Loves", category: "truck_stop", lat: 38.56, lng: -121.45, tags: [] }],
};

describe("isFresh", () => {
  test("younger than 12 h is fresh, 12 h and older is not", () => {
    expect(isFresh(T0, T0)).toBe(true);
    expect(isFresh(T0, T0 + 11 * H + 59 * 60 * 1000)).toBe(true);
    expect(isFresh(T0, T0 + MAX_AGE_MS)).toBe(false);
    expect(isFresh(T0, T0 + 13 * H)).toBe(false);
  });

  test("a saved time in the future or not a number is not fresh", () => {
    expect(isFresh(T0 + 1000, T0)).toBe(false);
    expect(isFresh(NaN, T0)).toBe(false);
    expect(isFresh(Infinity, T0)).toBe(false);
  });
});

describe("encode / decode", () => {
  test("the JSON round-trip keeps alternatives, via, stops, parking and the advisories", () => {
    const back = decodeSaved(JSON.parse(JSON.stringify(encodeSaved(active, T0))));
    expect(back).not.toBeNull();
    expect(back!.savedAt).toBe(T0);
    expect(back!.active).toEqual(active);
    expect(back!.active.route.alternatives?.[0].handle).toBe("h-80");
    expect(back!.active.route.via).toEqual([[38.56, -121.45]]);
    expect(back!.active.stops?.[0].id).toBe("s1");
    expect(back!.active.route.maneuvers).toHaveLength(1);
    expect(back!.active.route.speed_limits).toEqual([{ index: 0, mph: 65 }]);
    expect(back!.active.route.chain_controls?.[0].status).toBe("R-2");
  });

  test("a JSON string (the storage wrapper's older double encoding) decodes too", () => {
    expect(decodeSaved(JSON.stringify(encodeSaved(active, T0)))?.savedAt).toBe(T0);
  });

  test("anything else is null", () => {
    expect(decodeSaved(null)).toBeNull();
    expect(decodeSaved("{nope")).toBeNull();
    expect(decodeSaved(42)).toBeNull();
    expect(decodeSaved({ active, savedAt: "yesterday" })).toBeNull();
    expect(decodeSaved({ active: { place: { id: "x" }, route: {} }, savedAt: T0 })).toBeNull();
    expect(decodeSaved({ active: { place: active.place, route: { geometry: "none" } }, savedAt: T0 })).toBeNull();
    expect(decodeSaved({ savedAt: T0 })).toBeNull();
  });
});

describe("save / load / clear (AsyncStorage mock)", () => {
  beforeEach(async () => {
    await AsyncStorage.clear();
    await clearSavedRoute();
  });

  test("a fresh route comes back with its saved time", async () => {
    await saveActiveRoute(active, T0);
    expect(getSavedAt()).toBe(T0);
    const saved = await loadActiveRoute(T0 + 2 * H);
    expect(saved?.active).toEqual(active);
    expect(saved?.savedAt).toBe(T0);
  });

  test("a stale route is dropped and cleared", async () => {
    await saveActiveRoute(active, T0);
    expect(await loadActiveRoute(T0 + 13 * H)).toBeNull();
    expect(await AsyncStorage.getItem(ACTIVE_ROUTE_KEY)).toBeNull();
    expect(getSavedAt()).toBeNull();
  });

  test("nothing saved, or garbage, is null", async () => {
    expect(await loadActiveRoute(T0)).toBeNull();
    await AsyncStorage.setItem(ACTIVE_ROUTE_KEY, "{broken");
    expect(await loadActiveRoute(T0)).toBeNull();
    expect(await AsyncStorage.getItem(ACTIVE_ROUTE_KEY)).toBeNull();
  });

  test("a touch moves the saved time and nothing else: the trip is 12 h from when the app last ran with it", async () => {
    await saveActiveRoute(active, T0, true); // Go at 2 pm
    await touchSavedRoute(T0 + 6 * H); // parked at 8 pm, the app goes to the background
    expect(getSavedAt()).toBe(T0 + 6 * H);
    const saved = await loadActiveRoute(T0 + 16 * H); // 6 am
    expect(saved?.savedAt).toBe(T0 + 6 * H);
    expect(saved?.driving).toBe(true);
    expect(saved?.active).toEqual(active);
    expect(await loadActiveRoute(T0 + 18 * H)).toBeNull(); // the 12 h limit itself is the same
  });

  test("a touch with nothing saved writes nothing, and one queued behind a clear does not bring the route back", async () => {
    await touchSavedRoute(T0);
    expect(await AsyncStorage.getItem(ACTIVE_ROUTE_KEY)).toBeNull();
    expect(getSavedAt()).toBeNull();
    await saveActiveRoute(active, T0);
    const cleared = clearSavedRoute();
    const touched = touchSavedRoute(T0 + H);
    await Promise.all([cleared, touched]);
    expect(await AsyncStorage.getItem(ACTIVE_ROUTE_KEY)).toBeNull();
    expect(getSavedAt()).toBeNull();
  });

  test("clear removes the key", async () => {
    await saveActiveRoute(active, T0);
    await clearSavedRoute();
    expect(await AsyncStorage.getItem(ACTIVE_ROUTE_KEY)).toBeNull();
    expect(await loadActiveRoute(T0 + 1000)).toBeNull();
  });
});

describe("savedAgeLabel", () => {
  test("seconds, minutes, hours", () => {
    expect(savedAgeLabel(null)).toBe("no saved route");
    expect(savedAgeLabel(T0, T0 + 40_000)).toBe("saved 40 s ago");
    expect(savedAgeLabel(T0, T0 + 5 * 60_000)).toBe("saved 5 min ago");
    expect(savedAgeLabel(T0, T0 + 2 * H + 5 * 60_000)).toBe("saved 2 h 05 min ago");
  });
});

describe("writes are serialised", () => {
  it("a save still in flight when the route is cleared does not bring it back", async () => {
    await AsyncStorage.clear();
    // Not jest.spyOn: the module is already a jest mock, and mockRestore() on a spy over a mock function
    // leaves setItem with no implementation for every later test in this file.
    const original = AsyncStorage.setItem;
    let release: () => void = () => {};
    const slow = new Promise<void>((r) => (release = r));
    AsyncStorage.setItem = jest.fn(async (k: string, v: string) => {
      AsyncStorage.setItem = original; // only the first write hangs in the bridge
      await slow;
      return original(k, v);
    }) as typeof AsyncStorage.setItem;
    try {
      const saving = saveActiveRoute(active, T0);
      const clearing = clearSavedRoute(); // "End route" a few ms later
      release();
      await Promise.all([saving, clearing]);
      expect(await AsyncStorage.getItem(ACTIVE_ROUTE_KEY)).toBeNull();
      expect(getSavedAt()).toBeNull();
    } finally {
      AsyncStorage.setItem = original;
    }
  });
});

test("the trip id rides along with the saved route", async () => {
  await AsyncStorage.clear();
  await saveActiveRoute({ ...active, trip_id: "trip-abc-0001" }, T0);
  const saved = await loadActiveRoute(T0 + H);
  expect(saved?.active.trip_id).toBe("trip-abc-0001");
});
