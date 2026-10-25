import { useEffect, useState } from "react";

import { storage } from "../utils/storage";

export type TruckProfile = { height_in: number; trailer_ft: number; weight_lb: number; hazmat: boolean };

export const DEFAULT_TRUCK: TruckProfile = { height_in: 162, trailer_ft: 53, weight_lb: 80000, hazmat: false };
const KEY = "truck_profile_v1";

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
