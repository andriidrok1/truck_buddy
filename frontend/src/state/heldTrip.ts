// A driver the server signed out in the middle of a drive (the session was closed, the token was turned down) has
// to sign in again, and must not lose the route for it. The saved route is set aside under the account's id (the
// token's `sub`); the pending trip events stay where they are, stamped with the same id (src/net/tripEvents.ts).
// When someone signs in: the same account gets the route back (the map offers to resume it, as after a restart),
// anyone else gets nothing and the copy is destroyed. "Sign out" by the driver and "Delete my data" never keep it.

import { tripLog } from "../net/tripEvents";
import { storage } from "../utils/storage";
import { HELD_TRIP_KEY } from "../utils/storage/pendingWipe";
import { ACTIVE_ROUTE_KEY, clearSavedRoute, decodeSaved } from "./routeStore";

type Held = { sub: string; route: Record<string, unknown> };

const parse = (raw: unknown): Held | null => {
  if (raw === null || typeof raw !== "object" || Array.isArray(raw)) return null;
  const o = raw as Record<string, unknown>;
  if (typeof o.sub !== "string" || !o.sub || o.route === null || typeof o.route !== "object") return null;
  return { sub: o.sub, route: o.route as Record<string, unknown> };
};

/** Sets the saved route aside for `sub`. False when there is no route on disk to keep. Call before the route is cleared. */
export async function holdTrip(sub: string): Promise<boolean> {
  const raw = await storage.getItem<Record<string, unknown> | string | null>(ACTIVE_ROUTE_KEY, null);
  const saved = decodeSaved(raw);
  if (!saved) return false;
  return storage.setItem(HELD_TRIP_KEY, { sub, route: saved as unknown as Record<string, unknown> });
}

/** No trip is waiting for anyone (the driver signed out themselves). */
export const dropHeldTrip = (): Promise<boolean> => storage.removeItem(HELD_TRIP_KEY);

/**
 * At sign-in, before the token is stored (so the map finds the route when it mounts): `sub` is who signed in.
 * "restored": the same account, its route is back on disk. "discarded": someone else, the route and the pending
 * events of the driver before are gone. "none": nothing was waiting.
 */
export async function settleHeldTrip(sub: string | null): Promise<"restored" | "discarded" | "none"> {
  const held = parse(await storage.getItem<Record<string, unknown> | null>(HELD_TRIP_KEY, null));
  if (!held) return "none";
  if (sub !== null && held.sub === sub) {
    await storage.setItem(ACTIVE_ROUTE_KEY, held.route);
    await storage.removeItem(HELD_TRIP_KEY);
    return "restored";
  }
  // The copy first: a kill after this line leaves nothing of the previous driver's route for anyone.
  await storage.removeItem(HELD_TRIP_KEY);
  await clearSavedRoute();
  await tripLog.clear();
  return "discarded";
}
