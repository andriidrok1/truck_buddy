import { useEffect, useState } from "react";

import { storage } from "../utils/storage";

export type TruckProfile = {
  height_in: number;
  trailer_ft: number;
  weight_lb: number;
  hazmat: boolean;
  // Driver preferences collected during onboarding
  driver_name?: string | null;
  avoid_tolls?: boolean;
  avoid_ferries_tunnels?: boolean;
  favorite_chains?: string[];
  break_hours?: number | null;
  /** Tire chains on the truck; null = never asked (the copilot asks when a chain control is ahead). */
  chains_on_board?: boolean | null;
};

export const DEFAULT_TRUCK: TruckProfile = { height_in: 162, trailer_ft: 53, weight_lb: 80000, hazmat: false, chains_on_board: null };
const KEY = "truck_profile_v1";
const ONBOARDED_KEY = "onboarded_v1";

export async function isOnboarded() {
  return (await storage.getItem(ONBOARDED_KEY, false as boolean)) === true;
}

export async function setOnboarded() {
  await storage.setItem(ONBOARDED_KEY, true);
}

export function truckLabel(t: TruckProfile) {
  return `${Math.floor(t.height_in / 12)}'${t.height_in % 12}" · ${t.trailer_ft}' · ${Math.round(t.weight_lb / 1000)}k lb`;
}

// Tiny shared store so the editor screen and the map screen stay in sync.
let current: TruckProfile = DEFAULT_TRUCK;
let loaded = false;
const listeners = new Set<(t: TruckProfile) => void>();

/**
 * What `storage.getItem(KEY)` hands back. Older builds saved `JSON.stringify(profile)` through a
 * wrapper that stringifies again, so the stored document was a JSON string holding JSON; current
 * builds save the object itself. Both decode here; anything else (corrupt, wrong type) is null.
 */
export function parseStoredProfile(raw: unknown): Partial<TruckProfile> | null {
  let value: unknown = raw;
  try {
    // Up to two string layers: the double-encoded form, then the single-encoded one.
    for (let i = 0; i < 2 && typeof value === "string"; i++) value = JSON.parse(value);
  } catch {
    return null;
  }
  if (value === null || typeof value !== "object" || Array.isArray(value)) return null;
  return value as Partial<TruckProfile>;
}

let loading: Promise<void> | null = null;
let savedSinceLoad = false; // a save while the disk read is in flight wins over what the read returns

async function load() {
  if (loaded) return;
  loaded = true;
  const raw = await storage.getItem<Record<string, unknown> | string | null>(KEY, null);
  const parsed = parseStoredProfile(raw);
  if (parsed && !savedSinceLoad) {
    current = { ...DEFAULT_TRUCK, ...parsed };
    listeners.forEach((l) => l(current));
  }
}

/**
 * The saved profile, loaded if it isn't yet. Every request that carries the truck awaits this: until the
 * store has read it, `current` is the 13'6" default, and a route planned for the default is a route a
 * 14-footer can't take. Storage failures resolve to whatever is in memory (never a rejected request).
 */
export function ensureTruckProfile(): Promise<TruckProfile> {
  if (!loading) loading = load().catch(() => undefined);
  return loading.then(() => current);
}

export async function saveTruckProfile(t: TruckProfile) {
  loaded = true; // a save is newer than anything on disk; a late or in-flight load must not overwrite it
  savedSinceLoad = true;
  if (!loading) loading = Promise.resolve();
  current = t;
  listeners.forEach((l) => l(t));
  await storage.setItem(KEY, t); // the wrapper serialises once; load() still reads the older double-encoded form
}

export function getTruckProfile() {
  return current;
}

// Settings (hands-free, nudges, speed alerts, voice guidance, the truck editor's reroute flag) moved to
// src/state/settingsStore.ts; these names stay for the call sites.
export { requestTruckReroute, setHandsFree, setNudgesEnabled, setSpeedAlerts, takeTruckReroute, useHandsFree, useNudgesEnabled, useSpeedAlerts } from "../state/settingsStore";

export function useTruckProfile() {
  const [truck, setTruck] = useState<TruckProfile>(current);
  useEffect(() => {
    listeners.add(setTruck);
    ensureTruckProfile(); // the same single read the requests await, whichever effect runs first
    return () => {
      listeners.delete(setTruck);
    };
  }, []);
  return truck;
}
