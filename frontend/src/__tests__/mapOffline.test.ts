import type { MapLibre } from "../map/libre";
import { createMapLibrePacks, MAX_PACKS, noopPacks, offlinePacks, packsToDrop, type PackInfo } from "../map/offline";
import type { PackSpec } from "../map/bounds";

jest.mock("../map/libre", () => ({ loadMapLibre: () => null }));

const spec = (name: string): PackSpec => ({ name, bounds: { sw: { lat: 38, lng: -122 }, ne: { lat: 39, lng: -121 } }, minZoom: 6, maxZoom: 14 });
const info = (name: string, createdAt: number): Pick<PackInfo, "name" | "createdAt"> => ({ name, createdAt });

describe("no-op packs (Expo Go / Apple provider)", () => {
  test("lists nothing, removes nothing, refuses to download", async () => {
    expect(noopPacks.available).toBe(false);
    expect(await noopPacks.list()).toEqual([]);
    await expect(noopPacks.remove("route-1")).resolves.toBeUndefined();
    await expect(noopPacks.download(spec("route-1"))).rejects.toThrow(/MapLibre build/);
  });

  test("the app-level instance is the no-op one without the native module", () => {
    expect(offlinePacks.available).toBe(false);
    expect(offlinePacks).toBe(noopPacks);
  });
});

describe("packsToDrop (keep the 3 most recent)", () => {
  test("nothing to drop with room left; the oldest go first", () => {
    expect(packsToDrop([], "route-n")).toEqual([]);
    expect(packsToDrop([info("a", 1), info("b", 2)], "route-n")).toEqual([]);
    expect(packsToDrop([info("c", 3), info("a", 1), info("b", 2)], "route-n")).toEqual(["a"]);
    expect(packsToDrop([info("a", 1), info("b", 2), info("c", 3), info("d", 4)], "route-n")).toEqual(["a", "b"]);
  });

  test("re-downloading an existing name does not count it against the limit", () => {
    expect(packsToDrop([info("a", 1), info("b", 2), info("c", 3)], "b")).toEqual([]);
    expect(MAX_PACKS).toBe(3);
  });
});

/** A fake OfflineManager with the v11 surface the wrapper uses. Downloads finish on `complete(id)`. */
function fakeManager() {
  type Status = { id: string; state: "inactive" | "active" | "complete"; percentage: number; completedResourceSize: number; completedTileSize: number; completedResourceCount: number; completedTileCount: number; requiredResourceCount: number };
  type Listener = { progress: (p: unknown, s: Status) => void; error: (p: unknown, e: { id: string; message: string }) => void };
  const packs = new Map<string, { id: string; metadata: Record<string, unknown>; bounds: unknown; status: Status }>();
  const listeners = new Map<string, Listener>();
  let seq = 0;
  const tileLimits: number[] = [];
  const manager = {
    async getPacks() {
      return [...packs.values()].map((p) => ({ id: p.id, metadata: p.metadata, bounds: p.bounds, status: async () => p.status }));
    },
    async deletePack(id: string) {
      packs.delete(id);
    },
    async createPack(opts: { metadata?: Record<string, unknown>; bounds: unknown; minZoom?: number; maxZoom?: number; mapStyle: string }, progress: Listener["progress"], error: Listener["error"]) {
      const id = `pk-${++seq}`;
      const status: Status = { id, state: "active", percentage: 0, completedResourceSize: 0, completedTileSize: 0, completedResourceCount: 0, completedTileCount: 0, requiredResourceCount: 100 };
      packs.set(id, { id, metadata: opts.metadata ?? {}, bounds: opts.bounds, status });
      listeners.set(id, { progress, error });
      return { id, metadata: opts.metadata ?? {}, bounds: opts.bounds, status: async () => status };
    },
    removeListener(id: string) {
      listeners.delete(id);
    },
    setTileCountLimit(n: number) {
      tileLimits.push(n);
    },
    /** Test control: push progress / completion / an error for a pack. */
    progress(id: string, percentage: number, bytes: number) {
      const p = packs.get(id)!;
      p.status = { ...p.status, percentage, completedResourceSize: bytes, state: percentage >= 100 ? "complete" : "active" };
      listeners.get(id)?.progress(p, p.status);
    },
    fail(id: string, message: string) {
      listeners.get(id)?.error(packs.get(id), { id, message });
    },
    ids: () => [...packs.keys()],
    names: () => [...packs.values()].map((p) => p.metadata.name),
    listening: () => [...listeners.keys()],
    tileLimits,
  };
  return manager;
}

describe("createMapLibrePacks", () => {
  test("download: progress, completion, then listed as saved", async () => {
    const m = fakeManager();
    let t = 1000;
    const packs = createMapLibrePacks({ OfflineManager: m as unknown as MapLibre["OfflineManager"] }, "https://style", () => t++);
    expect(packs.available).toBe(true);
    const seen: number[] = [];
    const done = packs.download(spec("route-a"), (p) => seen.push(p.percentage));
    await new Promise((r) => setTimeout(r, 0));
    const [id] = m.ids();
    m.progress(id, 40, 4000);
    m.progress(id, 100, 10000);
    const result = await done;
    expect(seen).toEqual([40, 100]);
    expect(result).toEqual({ name: "route-a", bytes: 10000, createdAt: 1000, complete: true, percentage: 100 });
    expect(m.listening()).toEqual([]); // listener removed
    expect(m.tileLimits[0]).toBeGreaterThan(6000);
    expect(await packs.list()).toEqual([{ name: "route-a", bytes: 10000, createdAt: 1000, complete: true, percentage: 100 }]);
  });

  test("an error rejects and unsubscribes", async () => {
    const m = fakeManager();
    const packs = createMapLibrePacks({ OfflineManager: m as unknown as MapLibre["OfflineManager"] });
    const done = packs.download(spec("route-b"));
    await new Promise((r) => setTimeout(r, 0));
    m.fail(m.ids()[0], "tile limit");
    await expect(done).rejects.toThrow("tile limit");
    expect(m.listening()).toEqual([]);
  });

  test("LRU: the fourth pack drops the oldest; a finished pack of the same name is returned, not re-downloaded", async () => {
    const m = fakeManager();
    let t = 1;
    const packs = createMapLibrePacks({ OfflineManager: m as unknown as MapLibre["OfflineManager"] }, "https://style", () => t++);
    for (const name of ["route-1", "route-2", "route-3"]) {
      const d = packs.download(spec(name));
      await new Promise((r) => setTimeout(r, 0));
      m.progress(m.ids()[m.ids().length - 1], 100, 100);
      await d;
    }
    expect(m.names()).toEqual(["route-1", "route-2", "route-3"]);
    const d4 = packs.download(spec("route-4"));
    await new Promise((r) => setTimeout(r, 0));
    expect(m.names()).toEqual(["route-2", "route-3", "route-4"]);
    m.progress(m.ids()[m.ids().length - 1], 100, 100);
    await d4;
    const before = m.ids().length;
    const again = await packs.download(spec("route-3"));
    expect(again.complete).toBe(true);
    expect(m.ids().length).toBe(before);
    await packs.remove("route-2");
    expect(m.names()).toEqual(["route-3", "route-4"]);
  });

  test("packs that are not ours are ignored", async () => {
    const m = fakeManager();
    await m.createPack({ metadata: { name: "something-else" }, bounds: [0, 0, 1, 1], mapStyle: "s" }, () => {}, () => {});
    const packs = createMapLibrePacks({ OfflineManager: m as unknown as MapLibre["OfflineManager"] });
    expect(await packs.list()).toEqual([]);
  });
});
