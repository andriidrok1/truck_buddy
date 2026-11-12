// Fetches the weather along the active route (POST /api/weather/points) when the route changes and again every
// REFRESH_MS while it is active; keeps the last answer when a refresh fails (no signal on the pass).

import { useEffect, type RefObject } from "react";

import { api } from "../api";
import { routeKeyOf, useRouteWeatherStore } from "../state/routeWeatherStore";
import type { LatLng, RouteInfo } from "../types";
import { nearestIndex, samplePoints } from "../weather/routeWeather";

export const REFRESH_MS = 30 * 60 * 1000;

export function useRouteWeather(route: RouteInfo | null | undefined, trailerEmpty: boolean | null | undefined, coordsRef: RefObject<LatLng>) {
  const key = route ? routeKeyOf(route.geometry as [number, number][], route.duration_s) : null;
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
      // From where the truck is now (a refresh mid-drive, a route restored after an app restart), at times from now.
      const g = route.geometry as [number, number][];
      const samples = samplePoints(g, route.duration_s, new Date(), undefined, undefined, nearestIndex(g, coordsRef.current));
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
