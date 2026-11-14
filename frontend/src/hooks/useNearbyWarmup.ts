// Two effects of the map screen that look around the driver before any route (moved verbatim from app/index.tsx).
import { useEffect, useRef, type RefObject } from "react";

import { api } from "../api";
import { useTripStore } from "../state/tripStore";
import type { ActiveRoute, LatLng, Phase, Place, ProvidersResult } from "../types";

const trip = useTripStore.getState;

type Fix = { coords: LatLng; isDemo: boolean; resolved: boolean };

/** Within ~5 km of where the last look-around was made (a flat-earth check, lng shrunk by cos 37°). */
const nearSpot = (spot: LatLng | null, at: LatLng) => !!spot && Math.hypot(spot.lat - at.lat, (spot.lng - at.lng) * 0.8) < 0.045;

/**
 * Warm Places + parking caches around the driver: app on screen, no active route,
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

/** After a failed /places the start pins wait this long before trying again, doubling up to the cap. */
export const START_PINS_RETRY_MS = 30_000;
export const START_PINS_RETRY_MAX_MS = 5 * 60_000;

/**
 * The first map is not empty: while nothing is on it (no route, no search, no pins yet) the truck stops
 * around the driver go on as pins, a tap routes there, and the camera widens once to show the nearest
 * three. They make way for any search or route and come back when the map is empty again (QA 10-28, U4).
 *
 * The foreground GPS watch publishes a fix every ~20 m, so the look-around is guarded: one request at a time,
 * the last answer is reused within ~5 km of where it was asked (no request at all), and a failed request
 * waits 30 s, then 1, 2, 4, 5 min before the next one. A request the map no longer wants is aborted.
 */
export function useStartPins(
  loc: Fix,
  s: { active: ActiveRoute | null; phase: Phase; optionsCount: number; providers: ProvidersResult | null; placesCount: number },
  phaseRef: RefObject<Phase>,
  setFit: (fit: { key: string; points: LatLng[] }) => void,
) {
  const { active, phase, optionsCount, providers, placesCount } = s;
  const lastAnswer = useRef<{ at: LatLng; places: Place[] } | null>(null); // reused near the same spot (empty too)
  const inflight = useRef<AbortController | null>(null);
  const retry = useRef<{ at: number; waitMs: number } | null>(null); // after a failure: not before `at`

  const wanted = !active && phase === "ready" && !optionsCount && !providers && !placesCount && !(loc.isDemo && !loc.resolved);
  useEffect(() => {
    if (!wanted) {
      // A search or route took the map: the look-around in flight is of no use any more.
      inflight.current?.abort();
      inflight.current = null;
      return;
    }
    const at = loc.coords;
    const show = (places: Place[], from: LatLng) => {
      const t = trip();
      if (t.active || phaseRef.current !== "ready" || t.options.length || !places.length) return;
      const nearest = places.slice(0, 8);
      t.setStartPins(nearest);
      setFit({ key: `start-pins-${from.lat.toFixed(3)},${from.lng.toFixed(3)}`, points: [from, ...nearest.slice(0, 3)] });
    };
    const cached = lastAnswer.current;
    if (cached && nearSpot(cached.at, at)) {
      show(cached.places, cached.at);
      return;
    }
    if (inflight.current) return;
    if (retry.current && Date.now() < retry.current.at) return;
    const ctrl = new AbortController();
    inflight.current = ctrl;
    api
      .places("truck_stop", at, ctrl.signal)
      .then((res) => {
        if (ctrl.signal.aborted) return;
        retry.current = null;
        lastAnswer.current = { at, places: res.places };
        show(res.places, at);
      })
      .catch(() => {
        if (ctrl.signal.aborted) return;
        const waitMs = retry.current ? Math.min(retry.current.waitMs * 2, START_PINS_RETRY_MAX_MS) : START_PINS_RETRY_MS;
        retry.current = { at: Date.now() + waitMs, waitMs };
      })
      .finally(() => {
        if (inflight.current === ctrl) inflight.current = null;
      });
  }, [wanted, loc.coords, phaseRef, setFit]); // phaseRef, setFit: stable

  useEffect(
    () => () => {
      inflight.current?.abort();
      inflight.current = null;
    },
    [],
  );
}
