// Fetches the weather along the active route (POST /api/weather/points) when the route changes and again every
// REFRESH_MS while it is active; keeps the last answer when a refresh fails (no signal on the pass).

import { useEffect, useRef } from "react";

import { api } from "../api";
import { routeKeyOf, useRouteWeatherStore } from "../state/routeWeatherStore";
import type { RouteInfo } from "../types";
import { samplePoints } from "../weather/routeWeather";

export const REFRESH_MS = 30 * 60 * 1000;

export function useRouteWeather(route: RouteInfo | null | undefined, trailerEmpty: boolean | null | undefined) {
  const key = route ? routeKeyOf(route.geometry as [number, number][], route.duration_s) : null;
  // ETAs are counted from when this route was set (as the route's own duration is), so a refresh mid-drive
  // keeps the points' times instead of moving the whole route to "from now".
  const start = useRef<{ key: string | null; at: Date }>({ key: null, at: new Date() });
  if (start.current.key !== key) start.current = { key, at: new Date() };
  useEffect(() => {
    if (!route || !key) {
      useRouteWeatherStore.getState().clear();
      return;
    }
    if (useRouteWeatherStore.getState().routeKey !== key) useRouteWeatherStore.getState().clear();
    let ctl: AbortController | null = null;
    const load = () => {
      ctl?.abort();
      ctl = new AbortController();
      const samples = samplePoints(route.geometry as [number, number][], route.duration_s, start.current.at);
      api
        .weatherPoints(samples, trailerEmpty, ctl.signal)
        .then((r) => useRouteWeatherStore.getState().set(key, r.points, Date.now()))
        .catch(() => {});
    };
    load();
    const id = setInterval(load, REFRESH_MS);
    return () => {
      clearInterval(id);
      ctl?.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, trailerEmpty]);
}
