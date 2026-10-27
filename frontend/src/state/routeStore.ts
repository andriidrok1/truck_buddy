// The loaded route on disk, so a restart (or a crash in a dead zone) does not lose it. The map
// screen saves on every change of `active` and clears on "End route"; on mount it restores a
// document younger than MAX_AGE_MS. The whole ActiveRoute goes in: geometry, maneuvers, speed
// limits, incidents, weather, chain controls, alternatives, via stops and the parking result are
// everything the guidance and advisories read, so a restored route drives without the network.

import { useSyncExternalStore } from "react";

import type { ActiveRoute } from "../types";
import { storage } from "../utils/storage";

export const ACTIVE_ROUTE_KEY = "active_route_v1";
/** Older than this and the trip is over: a saved route is dropped, not restored. */
export const MAX_AGE_MS = 12 * 60 * 60 * 1000;

export type SavedRoute = { active: ActiveRoute; savedAt: number };

/** Younger than `maxAgeMs`. A `savedAt` in the future (clock set back) is not fresh either. */
export function isFresh(savedAt: number, now: number, maxAgeMs = MAX_AGE_MS): boolean {
  if (!Number.isFinite(savedAt)) return false;
  const age = now - savedAt;
  return age >= 0 && age < maxAgeMs;
}

export const encodeSaved = (active: ActiveRoute, savedAt: number): SavedRoute => ({ active, savedAt });

/**
 * What `storage.getItem(ACTIVE_ROUTE_KEY)` hands back, checked before it is trusted: a place
 * with an id and coordinates, a route with a geometry array, a numeric `savedAt`. Anything else
 * (older schema, corrupt write) is null and gets cleared by the loader.
 */
export function decodeSaved(raw: unknown): SavedRoute | null {
  let value: unknown = raw;
  try {
    if (typeof value === "string") value = JSON.parse(value);
  } catch {
    return null;
  }
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const { active, savedAt } = value as { active?: unknown; savedAt?: unknown };
  if (typeof savedAt !== "number" || !active || typeof active !== "object") return null;
  const { place, route } = active as { place?: Partial<ActiveRoute["place"]>; route?: Partial<ActiveRoute["route"]> };
  if (!place || typeof place.id !== "string" || typeof place.lat !== "number" || typeof place.lng !== "number") return null;
  if (!route || !Array.isArray(route.geometry)) return null;
  return { active: active as ActiveRoute, savedAt };
}

// When the route on disk was saved (null = nothing saved), for the tester screen.
let savedAt: number | null = null;
const listeners = new Set<() => void>();
const notify = () => listeners.forEach((l) => l());

export const getSavedAt = () => savedAt;

export async function saveActiveRoute(active: ActiveRoute, now = Date.now()) {
  savedAt = now;
  notify();
  await storage.setItem(ACTIVE_ROUTE_KEY, encodeSaved(active, now) as unknown as Record<string, unknown>);
}

/** The saved route if it is fresh; a stale or unreadable one is cleared and null comes back. */
export async function loadActiveRoute(now = Date.now()): Promise<SavedRoute | null> {
  const raw = await storage.getItem<Record<string, unknown> | string | null>(ACTIVE_ROUTE_KEY, null);
  const saved = decodeSaved(raw);
  if (!saved || !isFresh(saved.savedAt, now)) {
    await clearSavedRoute(); // stale, corrupt (the wrapper returns null for unparsable JSON) or missing
    return null;
  }
  savedAt = saved.savedAt;
  notify();
  return saved;
}

export async function clearSavedRoute() {
  savedAt = null;
  notify();
  await storage.removeItem(ACTIVE_ROUTE_KEY);
}

const subscribe = (l: () => void) => {
  listeners.add(l);
  return () => {
    listeners.delete(l);
  };
};

/** When the route on disk was last written, or null (tester screen). */
export function useSavedRouteAt(): number | null {
  return useSyncExternalStore(subscribe, getSavedAt, getSavedAt);
}

/** "saved 2 h 05 min ago" / "saved 40 s ago" / "no saved route" (tester screen). */
export function savedAgeLabel(savedAt: number | null, now = Date.now()): string {
  if (savedAt == null) return "no saved route";
  const s = Math.max(0, Math.round((now - savedAt) / 1000));
  if (s < 60) return `saved ${s} s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `saved ${m} min ago`;
  return `saved ${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")} min ago`;
}
