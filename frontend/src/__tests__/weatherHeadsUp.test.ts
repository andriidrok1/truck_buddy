import AsyncStorage from "@react-native-async-storage/async-storage";

import { ROUTE_WEATHER_KEY, restoreRouteWeather, SAVED_MAX_AGE_MS, useRouteWeatherStore } from "../state/routeWeatherStore";
import { themes } from "../theme";
import { chips, weatherAhead, type RoutePoint } from "../weather/routeWeather";
import { APP_KEYS } from "../utils/storage/pendingWipe";

const LINE: [number, number][] = Array.from({ length: 11 }, (_, i) => [38.5 + i * 0.1, -121.5 + i * 0.17]);
// 10 km between vertices, 100 km in all, driven in 100 min: 1 km a minute.
const CUM = LINE.map((_, i) => i * 10000);
const ROUTE = { distance_m: 100000, duration_s: 6000, weather: [], geometry: LINE };

function p(over: Partial<RoutePoint>): RoutePoint {
  return { index: 0, lat: 39, lng: -120, eta: "2026-11-11T23:50:00Z", kind: "clear", severity: "info", ice_risk: false, reasons: [], ...over } as RoutePoint;
}

beforeEach(async () => {
  await AsyncStorage.clear();
  useRouteWeatherStore.getState().clear();
});

describe("weatherAhead", () => {
  const list = chips([p({ index: 2, kind: "rain" }), p({ index: 4, kind: "ice", severity: "warn", reasons: ["ice"], temp_f: 31, place: "Truckee, CA" }), p({ index: 8, kind: "wind", severity: "warn", gust_mph: 60 })], ROUTE);

  it("the first point that matters ahead of the truck, minutes by distance", () => {
    expect(weatherAhead(list, ROUTE, CUM, 1, 15000)).toEqual({
      key: "ice-4", kind: "ice", place: "Truckee, CA", minutes_ahead: 25, temp_f: 31, gust_mph: null, vis_mi: null, event: null,
    });
  });

  it("skips what is already behind", () => {
    expect(weatherAhead(list, ROUTE, CUM, 5, 52000)?.key).toBe("wind-8");
    expect(weatherAhead(list, ROUTE, CUM, 9, 95000)).toBeNull();
  });

  it("nothing without a match on the route", () => {
    expect(weatherAhead(list, ROUTE, CUM, -1, 0)).toBeNull();
    expect(weatherAhead(list, ROUTE, [], 1, 0)).toBeNull();
  });
});

describe("saved forecast", () => {
  it("is restored for the same route only, and not when old", async () => {
    useRouteWeatherStore.getState().set("route-a", [p({ index: 3 })], 1_000_000);
    await new Promise((r) => setTimeout(r, 0));
    useRouteWeatherStore.getState().clear();
    expect(await restoreRouteWeather("route-b", 1_000_000)).toBe(false);
    expect(await restoreRouteWeather("route-a", 1_000_000 + SAVED_MAX_AGE_MS + 1)).toBe(false);
    expect(await restoreRouteWeather("route-a", 1_000_500)).toBe(true);
    expect(useRouteWeatherStore.getState().points).toHaveLength(1);
    expect(useRouteWeatherStore.getState().fetchedAt).toBe(1_000_000);
  });

  it("is wiped with Delete my data", () => {
    expect(APP_KEYS).toContain(ROUTE_WEATHER_KEY);
  });
});

test("the turn banner is light in the light theme", () => {
  expect(themes.light.navBanner).toBe("#FFFFFF");
  expect(themes.dark.navBanner).toBe("#2C2C2E");
  expect(themes.light.onNavBanner).toBe("#1C1C1E");
});
