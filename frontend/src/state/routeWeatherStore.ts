// The weather points of the active route (src/hooks/useRouteWeather.ts fills it; the map and the route card
// read it). Keyed by the route's geometry so a new route never shows the old one's chips.

import { create } from "zustand";

import { storage } from "../utils/storage";

import type { RoutePoint } from "../weather/routeWeather";

type State = {
  routeKey: string | null;
  points: RoutePoint[];
  /** When the points were fetched (ms); shown as "forecast from …" when there is no signal. */
  fetchedAt: number | null;
  /** The copilot turned the weather view on for a heads-up (Figma V1): shown over a plain map until the route
   *  ends or the driver picks a view; never written into the driver's stored setting. */
  autoWeather: boolean;
  setAutoWeather: (on: boolean) => void;
  set: (routeKey: string, points: RoutePoint[], at: number) => void;
  clear: () => void;
};

export const useRouteWeatherStore = create<State>()((set) => ({
  routeKey: null,
  points: [],
  fetchedAt: null,
  autoWeather: false,
  setAutoWeather: (autoWeather) => set({ autoWeather }),
  set: (routeKey, points, fetchedAt) => {
    set({ routeKey, points, fetchedAt });
    // On disk too: an app restarted on a pass with no signal still has the route's forecast (Figma V8).
    storage.setItem(ROUTE_WEATHER_KEY, { routeKey, points, fetchedAt });
  },
  clear: () => set({ routeKey: null, points: [], fetchedAt: null, autoWeather: false }),
}));

export const ROUTE_WEATHER_KEY = "route_weather_v1";
/** A saved forecast older than this is not shown. */
export const SAVED_MAX_AGE_MS = 12 * 60 * 60 * 1000;

/** The saved forecast for this route, into the store; false when there is none (or it is for another route). */
export async function restoreRouteWeather(routeKey: string, now = Date.now()): Promise<boolean> {
  const saved = await storage.getItem<{ routeKey?: string; points?: RoutePoint[]; fetchedAt?: number } | null>(ROUTE_WEATHER_KEY, null);
  if (!saved || saved.routeKey !== routeKey || !Array.isArray(saved.points) || !saved.fetchedAt || now - saved.fetchedAt > SAVED_MAX_AGE_MS) return false;
  if (useRouteWeatherStore.getState().routeKey === routeKey) return true; // a fresh answer came first
  useRouteWeatherStore.setState({ routeKey, points: saved.points, fetchedAt: saved.fetchedAt });
  return true;
}

/** A cheap identity for a route: its length, ends and duration. */
export function routeKeyOf(geometry: [number, number][], durationS: number): string {
  if (!geometry.length) return "";
  const a = geometry[0];
  const b = geometry[geometry.length - 1];
  return `${geometry.length}:${a[0].toFixed(4)},${a[1].toFixed(4)}:${b[0].toFixed(4)},${b[1].toFixed(4)}:${Math.round(durationS)}`;
}
