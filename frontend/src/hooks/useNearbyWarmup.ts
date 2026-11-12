// Two effects of the map screen that look around the driver before any route (moved verbatim from app/index.tsx).
import { useEffect, useRef, type RefObject } from "react";

import { api } from "../api";
import { useTripStore } from "../state/tripStore";
import type { ActiveRoute, LatLng, Phase, ProvidersResult } from "../types";

const trip = useTripStore.getState;

type Fix = { coords: LatLng; isDemo: boolean; resolved: boolean };

/** Within ~5 km of where the last look-around was made (a flat-earth check, lng shrunk by cos 37°). */
const nearSpot = (spot: LatLng | null, at: LatLng) => !!spot && Math.hypot(spot.lat - at.lat, (spot.lng - at.lng) * 0.8) < 0.045;

/**
 * Warm the Places cache around the driver (no satellite parking check): app on screen, no active route,
 * real GPS fix (or demo after location resolved), then again after moving ~5 km.
 */
export function useWarmCaches(loc: Fix, appActive: boolean, active: ActiveRoute | null) {
  const warmedAt = useRef<LatLng | null>(null);
  useEffect(() => {
    if (!appActive || active || (loc.isDemo && !loc.resolved)) return;
    if (nearSpot(warmedAt.current, loc.coords)) return;
    warmedAt.current = loc.coords;
    api.warm(loc.coords).catch(() => {});
  }, [loc.coords, loc.isDemo, loc.resolved, appActive, active]);
}

/**
 * The first map is not empty: while nothing is on it (no route, no search, no pins yet) the truck stops
 * around the driver go on as pins, a tap routes there, and the camera widens once to show the nearest
 * three. They make way for any search or route and come back when the map is empty again (QA 10-28, U4).
 */
export function useStartPins(
  loc: Fix,
  s: { active: ActiveRoute | null; phase: Phase; optionsCount: number; providers: ProvidersResult | null; placesCount: number },
  phaseRef: RefObject<Phase>,
  setFit: (fit: { key: string; points: LatLng[] }) => void,
) {
  const { active, phase, optionsCount, providers, placesCount } = s;
  const startPinsEmptyAt = useRef<LatLng | null>(null); // where a fetch found nothing: no retry at the same spot
  useEffect(() => {
    if (active || phase !== "ready" || optionsCount || providers || placesCount) return;
    if (loc.isDemo && !loc.resolved) return;
    if (nearSpot(startPinsEmptyAt.current, loc.coords)) return;
    const at = loc.coords;
    let cancelled = false;
    api
      .places("truck_stop", at)
      .then((res) => {
        const t = trip();
        if (cancelled || t.active || phaseRef.current !== "ready" || t.options.length) return;
        if (!res.places.length) {
          startPinsEmptyAt.current = at;
          return;
        }
        const nearest = res.places.slice(0, 8);
        t.setStartPins(nearest);
        setFit({ key: `start-pins-${at.lat.toFixed(3)},${at.lng.toFixed(3)}`, points: [at, ...nearest.slice(0, 3)] });
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [active, phase, optionsCount, providers, placesCount, loc.coords, loc.isDemo, loc.resolved, phaseRef, setFit]); // phaseRef, setFit: stable
}
