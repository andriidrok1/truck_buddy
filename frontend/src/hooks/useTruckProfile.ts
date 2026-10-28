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
};

export const DEFAULT_TRUCK: TruckProfile = { height_in: 162, trailer_ft: 53, weight_lb: 80000, hazmat: false };
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

async function load() {
  if (loaded) return;
  loaded = true;
  const raw = await storage.getItem<Record<string, unknown> | string | null>(KEY, null);
  const parsed = parseStoredProfile(raw);
  if (parsed) {
    current = { ...DEFAULT_TRUCK, ...parsed };
    listeners.forEach((l) => l(current));
  }
}

// The truck editor saved new dimensions: the map screen reroutes the active route once it is back in focus.
let rerouteRequested = false;
export const requestTruckReroute = () => {
  rerouteRequested = true;
};
export const takeTruckReroute = () => {
  const r = rerouteRequested;
  rerouteRequested = false;
  return r;
};


export async function saveTruckProfile(t: TruckProfile) {
  current = t;
  listeners.forEach((l) => l(t));
  await storage.setItem(KEY, t); // the wrapper serialises once; load() still reads the older double-encoded form
}

export function getTruckProfile() {
  return current;
}

// Hands-free wake word setting (on by default), stored next to the truck profile.
const HANDS_FREE_KEY = "hands_free_v1";
let handsFree = true;
let handsFreeLoaded = false;
const handsFreeListeners = new Set<(v: boolean) => void>();

export async function setHandsFree(v: boolean) {
  handsFree = v;
  handsFreeListeners.forEach((l) => l(v));
  await storage.setItem(HANDS_FREE_KEY, v);
}

export function useHandsFree() {
  const [value, setValue] = useState(handsFree);
  useEffect(() => {
    handsFreeListeners.add(setValue);
    if (!handsFreeLoaded) {
      handsFreeLoaded = true;
      storage.getItem(HANDS_FREE_KEY, true as boolean).then((v) => {
        handsFree = v !== false;
        handsFreeListeners.forEach((l) => l(handsFree));
      });
    }
    return () => {
      handsFreeListeners.delete(setValue);
    };
  }, []);
  return value;
}

// Speed limit alerts (off by default: posted limits come from map data and can be stale).
const SPEED_ALERTS_KEY = "speed_alerts_v1";
let speedAlerts = false;
let speedAlertsLoaded = false;
const speedAlertsListeners = new Set<(v: boolean) => void>();

export async function setSpeedAlerts(v: boolean) {
  speedAlerts = v;
  speedAlertsListeners.forEach((l) => l(v));
  await storage.setItem(SPEED_ALERTS_KEY, v);
}

export function useSpeedAlerts() {
  const [value, setValue] = useState(speedAlerts);
  useEffect(() => {
    speedAlertsListeners.add(setValue);
    if (!speedAlertsLoaded) {
      speedAlertsLoaded = true;
      storage.getItem(SPEED_ALERTS_KEY, false as boolean).then((v) => {
        speedAlerts = v === true;
        speedAlertsListeners.forEach((l) => l(speedAlerts));
      });
    }
    return () => {
      speedAlertsListeners.delete(setValue);
    };
  }, []);
  return value;
}

export function useTruckProfile() {
  const [truck, setTruck] = useState<TruckProfile>(current);
  useEffect(() => {
    listeners.add(setTruck);
    load();
    return () => {
      listeners.delete(setTruck);
    };
  }, []);
  return truck;
}
