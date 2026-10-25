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

async function load() {
  if (loaded) return;
  loaded = true;
  const raw = await storage.getItem(KEY, null);
  if (typeof raw === "string") {
    try {
      current = { ...DEFAULT_TRUCK, ...JSON.parse(raw) };
      listeners.forEach((l) => l(current));
    } catch {
      // keep defaults on a corrupt value
    }
  }
}

export async function saveTruckProfile(t: TruckProfile) {
  current = t;
  listeners.forEach((l) => l(t));
  await storage.setItem(KEY, JSON.stringify(t));
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
