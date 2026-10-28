// Offline corridor packs for the screens: the list (truck profile, the "Map saved" note) and the
// download state of the active route's pack (the place card). One module-level list shared by
// everything, refreshed after each download or delete.

import { useCallback, useEffect, useState, useSyncExternalStore } from "react";

import { corridorPack, estimateBytes, type PackSpec } from "../map/bounds";
import { offlinePacks, type PackInfo } from "../map/offline";
import type { RouteInfo } from "../types";

let packs: PackInfo[] = [];
let loaded = false;
const listeners = new Set<() => void>();
const notify = () => listeners.forEach((l) => l());
const subscribe = (l: () => void) => {
  listeners.add(l);
  return () => {
    listeners.delete(l);
  };
};
const getPacks = () => packs;

export async function refreshPacks() {
  if (!offlinePacks.available) return;
  try {
    packs = await offlinePacks.list();
  } catch (e) {
    console.log(`[map] listing packs failed: ${String(e)}`);
  }
  loaded = true;
  notify();
}

export async function removePack(name: string) {
  await offlinePacks.remove(name);
  await refreshPacks();
}

/** All packs on the device (empty without MapLibre). */
export function usePacks(): { packs: PackInfo[]; available: boolean } {
  const list = useSyncExternalStore(subscribe, getPacks, getPacks);
  useEffect(() => {
    if (!loaded) refreshPacks();
  }, []);
  return { packs: list, available: offlinePacks.available };
}

export type OfflineMapState =
  | { kind: "none"; estimateBytes: number }
  | { kind: "downloading"; percentage: number; bytes: number }
  | { kind: "saved"; bytes: number }
  | { kind: "failed"; estimateBytes: number };

/** The pack for `route`: its state and a download action. null without MapLibre or a route. */
export function useOfflineMap(route: RouteInfo | null): { state: OfflineMapState | null; pack: PackSpec | null; download: () => void } {
  const { packs: list, available } = usePacks();
  const pack = route && available ? corridorPack(route) : null;
  const saved = pack ? list.find((p) => p.name === pack.name && p.complete) : undefined;
  const [progress, setProgress] = useState<{ name: string; percentage: number; bytes: number; failed?: boolean } | null>(null);

  const download = useCallback(() => {
    if (!pack) return;
    const { name } = pack;
    setProgress({ name, percentage: 0, bytes: 0 });
    offlinePacks
      .download(pack, (p) => setProgress({ name, percentage: p.percentage, bytes: p.bytes }))
      .then(() => refreshPacks())
      .then(() => setProgress(null))
      .catch((e) => {
        console.log(`[map] pack ${name} failed: ${String(e)}`);
        setProgress({ name, percentage: 0, bytes: 0, failed: true });
      });
  }, [pack]);

  let state: OfflineMapState | null = null;
  if (pack) {
    const estimate = estimateBytes(pack.bounds, pack.minZoom, pack.maxZoom);
    if (saved) state = { kind: "saved", bytes: saved.bytes };
    else if (progress && progress.name === pack.name && progress.failed) state = { kind: "failed", estimateBytes: estimate };
    else if (progress && progress.name === pack.name) state = { kind: "downloading", percentage: progress.percentage, bytes: progress.bytes };
    else state = { kind: "none", estimateBytes: estimate };
  }
  return { state, pack, download };
}
