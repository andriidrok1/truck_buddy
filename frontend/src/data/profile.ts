// Server-side resources through react-query. The truck profile is the first: the server copy is the
// truth once the backend has `/api/me/profile` (fleet-managed profiles land there); until then, and
// whenever the network is away, the local copy in hooks/useTruckProfile.ts is what the app runs on.

import { useQuery } from "@tanstack/react-query";

import { api, ApiError } from "../api";
import { DEFAULT_TRUCK, ensureTruckProfile, getTruckProfile, saveTruckProfile, type TruckProfile } from "../hooks/useTruckProfile";
import { queryClient } from "../query-client";
import { storage } from "../utils/storage";
import type { TripEventRow } from "../api";

export const PROFILE_KEY = ["profile"] as const;
export const tripEventsKey = (tripId: string) => ["trip-events", tripId] as const;

/** The server has no profile endpoint yet (or no profile for this driver): the local copy stands. */
export const isMissing = (e: unknown) => e instanceof ApiError && (e.status === 404 || e.status === 405);
/** The server looked at the profile and said no (422 and the like): sending it again will not help. */
const isRefused = (e: unknown) => e instanceof ApiError && e.status >= 400 && e.status < 500 && !isMissing(e);

/** The profile the server has not confirmed yet, as sent: written before the request, so a kill mid-request
 *  leaves it behind and the next start sends the profile instead of pulling the older server copy over it. */
export const UNSYNCED_KEY = "profile_unsynced_v1";

let midTrip = () => false;
/** The map screen says whether a trip is on (this module does not reach into the trip store). */
export const setMidTripCheck = (check: () => boolean) => {
  midTrip = check;
};

let edits = 0; // bumped by every local save: a server read that started before an edit must not undo it

const stamp = (p: TruckProfile) => JSON.stringify(p);

async function push(p: TruckProfile): Promise<void> {
  const sent = stamp(p);
  await storage.setItem(UNSYNCED_KEY, sent);
  try {
    await api.saveProfile(p);
  } catch (e) {
    if (!isMissing(e) && !isRefused(e)) throw e; // offline or a 5xx: the flag stays for the next start
    if (isRefused(e)) console.warn("[profile] the server refused the profile", e);
    // no endpoint (404) or refused: nothing more to send, the flag goes below
  }
  // Only this save's flag: a newer save made while this request ran has its own and keeps it.
  if ((await storage.getItem<string | null>(UNSYNCED_KEY, null)) === sent) await storage.removeItem(UNSYNCED_KEY);
}

/** The profile from the server, else the local one; the local copy is kept in step with what the server says.
 *  A change the server never got is sent first, so the older server copy does not undo it. */
export async function fetchProfile(): Promise<TruckProfile> {
  const local = await ensureTruckProfile();
  try {
    if (await storage.getItem<string | null>(UNSYNCED_KEY, null)) {
      await push(local);
      return getTruckProfile();
    }
    const before = edits;
    const { profile: remote, source } = await api.profile();
    if (edits !== before) return getTruckProfile(); // the driver saved while this was in flight: theirs is newer
    // A fleet's truck: mid-trip the phone's sizes are the trip's (a swapped trailer) and the truck's stored sizes
    // must not undo them; between trips the truck's come back (decision 2026-11-02, bug 2).
    if (source === "vehicle" && midTrip()) return getTruckProfile();
    const cur = getTruckProfile();
    const merged = { ...cur, ...remote };
    if (stamp(merged) !== stamp(cur)) await saveTruckProfile(merged);
    return merged;
  } catch (e) {
    if (isMissing(e)) return getTruckProfile();
    throw e;
  }
}

/** Every save of the truck: locally at once (the next request sees the new truck), then the server in the
 *  background; offline, the change waits behind UNSYNCED_KEY for the next start. */
export async function saveProfile(next: TruckProfile): Promise<void> {
  edits++;
  await saveTruckProfile(next);
  queryClient.setQueryData(PROFILE_KEY, next);
  push(next).catch(() => {});
}

/** "Delete my data": the default truck locally and nothing pending for the server (the server drops its copy
 *  with the rest of the driver's data). */
export async function resetProfile(): Promise<void> {
  edits++;
  await saveTruckProfile(DEFAULT_TRUCK);
  queryClient.setQueryData(PROFILE_KEY, DEFAULT_TRUCK);
  await storage.removeItem(UNSYNCED_KEY);
}

/** The trip log rows the backend holds for one drive (tester screen). */
export function useTripEvents(tripId: string | null | undefined, enabled = true) {
  return useQuery<TripEventRow[]>({
    queryKey: tripEventsKey(tripId ?? "none"),
    queryFn: () => api.tripEventsOf(tripId as string),
    enabled: enabled && !!tripId,
    staleTime: 15 * 1000,
  });
}
