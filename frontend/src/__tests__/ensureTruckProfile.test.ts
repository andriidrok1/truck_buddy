/**
 * Every request that carries the truck awaits ensureTruckProfile(): until the store has read the saved
 * profile, the in-memory value is the 13'6" default, and a route planned for the default is one a taller
 * rig can't take. A save made before the load lands must win over the stale disk copy.
 */
jest.mock("../utils/storage", () => {
  let resolveGet: ((v: unknown) => void) | null = null;
  const pending = new Promise((r) => {
    resolveGet = r;
  });
  return {
    storage: {
      getItem: jest.fn(() => pending),
      setItem: jest.fn(async () => undefined),
      __resolve: (v: unknown) => resolveGet?.(v),
    },
  };
});

import { storage } from "../utils/storage";
import { DEFAULT_TRUCK, ensureTruckProfile, getTruckProfile, saveTruckProfile } from "../hooks/useTruckProfile";

const stored = { height_in: 169, trailer_ft: 53, weight_lb: 80000, hazmat: false };

test("ensureTruckProfile waits for the saved profile instead of handing out the default", async () => {
  expect(getTruckProfile()).toEqual(DEFAULT_TRUCK); // what a request would have sent before
  const p = ensureTruckProfile();
  let settled = false;
  p.then(() => {
    settled = true;
  });
  await Promise.resolve();
  expect(settled).toBe(false); // still reading storage: the request waits
  (storage as unknown as { __resolve: (v: unknown) => void }).__resolve(stored);
  const t = await p;
  expect(t.height_in).toBe(169);
  expect(getTruckProfile().height_in).toBe(169);
  expect(storage.getItem).toHaveBeenCalledTimes(1);
  await expect(ensureTruckProfile()).resolves.toMatchObject({ height_in: 169 }); // no second read
});

test("a save before the load finishes is not overwritten by the disk copy", async () => {
  await saveTruckProfile({ ...DEFAULT_TRUCK, height_in: 150 });
  await expect(ensureTruckProfile()).resolves.toMatchObject({ height_in: 150 });
});
