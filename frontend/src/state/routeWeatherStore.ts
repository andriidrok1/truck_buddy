// The weather points of the active route (src/hooks/useRouteWeather.ts fills it; the map and the route card
// read it). Keyed by the route's geometry so a new route never shows the old one's chips.

import { create } from "zustand";

import type { RoutePoint } from "../weather/routeWeather";

type State = {
  routeKey: string | null;
  points: RoutePoint[];
  /** When the points were fetched (ms); shown as "forecast from …" when there is no signal. */
  fetchedAt: number | null;
  set: (routeKey: string, points: RoutePoint[], at: number) => void;
  clear: () => void;
};

export const useRouteWeatherStore = create<State>()((set) => ({
  routeKey: null,
  points: [],
  fetchedAt: null,
  set: (routeKey, points, fetchedAt) => set({ routeKey, points, fetchedAt }),
  clear: () => set({ routeKey: null, points: [], fetchedAt: null }),
}));

/** A cheap identity for a route: its length, ends and duration. */
export function routeKeyOf(geometry: [number, number][], durationS: number): string {
  if (!geometry.length) return "";
  const a = geometry[0];
  const b = geometry[geometry.length - 1];
  return `${geometry.length}:${a[0].toFixed(4)},${a[1].toFixed(4)}:${b[0].toFixed(4)},${b[1].toFixed(4)}:${Math.round(durationS)}`;
}
