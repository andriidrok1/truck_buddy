// Offline corridor packs behind one small interface. The MapLibre implementation wraps
// OfflineManager (v11: createPack / getPacks / deletePack / addListener / removeListener; packs
// get a native UUID, our name lives in `metadata.name`). Without MapLibre (Expo Go, the Apple
// provider) the no-op implementation lists nothing and refuses to download. At most MAX_PACKS
// packs are kept: the oldest goes before a new one is created. No state-wide pack: at z14 a
// state is gigabytes.

import { loadMapLibre, type MapLibre } from "./libre";
import { MAP_PROVIDER, OPENFREEMAP_STYLE } from "./provider";
import { toLngLatBounds, type PackSpec } from "./bounds";

export const MAX_PACKS = 3;
export const PACK_PREFIX = "route-";
/** MapLibre's default per-device tile limit is 6000, too few for a long corridor at z14. */
const TILE_COUNT_LIMIT = 40000;

export type PackInfo = {
  name: string;
  /** Bytes on disk so far (all of them once complete). */
  bytes: number;
  /** When the pack was created (metadata), epoch ms; 0 when unknown. */
  createdAt: number;
  complete: boolean;
  percentage: number;
};

export type PackProgress = { percentage: number; bytes: number };

export interface OfflinePacks {
  /** False for the no-op implementation: hide the download UI. */
  readonly available: boolean;
  list(): Promise<PackInfo[]>;
  /** Downloads (or returns the finished pack of the same name); drops the oldest packs beyond MAX_PACKS first. */
  download(pack: PackSpec, onProgress?: (p: PackProgress) => void): Promise<PackInfo>;
  remove(name: string): Promise<void>;
}

export const noopPacks: OfflinePacks = {
  available: false,
  list: async () => [],
  download: async () => {
    throw new Error("Offline maps need the MapLibre build (EXPO_PUBLIC_MAP_PROVIDER=maplibre)");
  },
  remove: async () => {},
};

/** Names to delete so that, with `incoming` added, at most `max` packs remain: the oldest first. */
export function packsToDrop(existing: readonly Pick<PackInfo, "name" | "createdAt">[], incoming: string, max = MAX_PACKS): string[] {
  const others = existing.filter((p) => p.name !== incoming).sort((a, b) => a.createdAt - b.createdAt);
  const room = Math.max(0, max - 1);
  return others.slice(0, Math.max(0, others.length - room)).map((p) => p.name);
}

type NativePack = Awaited<ReturnType<MapLibre["OfflineManager"]["getPacks"]>>[number];

const metaName = (p: NativePack) => (typeof p.metadata?.name === "string" ? p.metadata.name : "");
const metaCreated = (p: NativePack) => (typeof p.metadata?.createdAt === "number" ? p.metadata.createdAt : 0);

export function createMapLibrePacks(lib: Pick<MapLibre, "OfflineManager">, styleUrl = OPENFREEMAP_STYLE, now: () => number = Date.now): OfflinePacks {
  const { OfflineManager } = lib;
  const ours = async () => (await OfflineManager.getPacks()).filter((p) => metaName(p).startsWith(PACK_PREFIX));
  const info = async (p: NativePack): Promise<PackInfo> => {
    const s = await p.status();
    return {
      name: metaName(p),
      bytes: s.completedResourceSize,
      createdAt: metaCreated(p),
      complete: s.state === "complete" || s.percentage >= 100,
      percentage: s.percentage,
    };
  };
  const remove = async (name: string) => {
    for (const p of await ours()) if (metaName(p) === name) await OfflineManager.deletePack(p.id);
  };
  return {
    available: true,
    list: async () => Promise.all((await ours()).map(info)),
    remove,
    async download(pack, onProgress) {
      const existing = await ours();
      const same = existing.find((p) => metaName(p) === pack.name);
      if (same) {
        const i = await info(same);
        if (i.complete) return i;
        await OfflineManager.deletePack(same.id); // a half pack from an earlier try: start over
      }
      const current = await Promise.all(existing.filter((p) => p !== same).map(info));
      for (const name of packsToDrop(current, pack.name)) await remove(name);
      OfflineManager.setTileCountLimit(TILE_COUNT_LIMIT);
      return new Promise<PackInfo>((resolve, reject) => {
        let settled = false;
        let id: string | null = null;
        const finish = (fn: () => void) => {
          if (settled) return;
          settled = true;
          if (id) OfflineManager.removeListener(id);
          fn();
        };
        OfflineManager.createPack(
          {
            mapStyle: styleUrl,
            bounds: toLngLatBounds(pack.bounds),
            minZoom: pack.minZoom,
            maxZoom: pack.maxZoom,
            metadata: { name: pack.name, createdAt: now() },
          },
          (p, s) => {
            onProgress?.({ percentage: s.percentage, bytes: s.completedResourceSize });
            if (s.state === "complete" || s.percentage >= 100) {
              finish(() => resolve({ name: pack.name, bytes: s.completedResourceSize, createdAt: metaCreated(p), complete: true, percentage: 100 }));
            }
          },
          (_p, err) => finish(() => reject(new Error(err.message))),
        )
          .then((p) => {
            id = p.id;
          })
          .catch((e) => finish(() => reject(e instanceof Error ? e : new Error(String(e)))));
      });
    },
  };
}

function pick(): OfflinePacks {
  const lib = MAP_PROVIDER === "maplibre" ? loadMapLibre() : null;
  return lib ? createMapLibrePacks(lib) : noopPacks;
}

/** The app's packs: MapLibre's when that provider is live, the no-op otherwise. */
export const offlinePacks: OfflinePacks = pick();
