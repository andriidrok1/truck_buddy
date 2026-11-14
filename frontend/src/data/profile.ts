// Server-side resources through react-query. The truck profile is the first: the server copy is the
// truth once the backend has `/api/me/profile` (fleet-managed profiles land there); until then, and
// whenever the network is away, the local copy in hooks/useTruckProfile.ts is what the app runs on.

import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { AppState } from "react-native";

import { useNetStore } from "../state/netStore";

import { api, ApiError } from "../api";
import { DEFAULT_TRUCK, ensureTruckProfile, getTruckProfile, saveTruckProfile, type TruckProfile } from "../hooks/useTruckProfile";
import { queryClient } from "../query-client";
import { confirmHeight } from "../truck/heightConfirmed";
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
let wipes = 0; // bumped when the phone changes hands: an answer for the driver before is not for the one after

const stamp = (p: unknown) => JSON.stringify(p);

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

// ---- A returning driver's first pull (after sign-in on a new phone or a reinstall) ----
//
// Until the server's profile has landed, the phone holds the default truck. Anything saved in that window (the
// height card, a setting) used to count as "the driver's newer edit": the pull threw the server copy away and the
// default truck was pushed over it. So while a restore is pending nothing is pushed: a save is kept as a patch,
// the server copy is the base when it lands and the patch goes on top. The flag is on disk: a kill in the middle
// comes back to the same state (and to "Welcome back", which waits for the pull).

export const RESTORE_KEY = "profile_restore_v1";

let restoring: boolean | undefined; // undefined = not read yet
let patch: Partial<TruckProfile> = {};
let serverCopy: TruckProfile | null = null; // what the server last returned for this driver; null = nothing yet, or a 404
let onSettled: ((found: boolean) => unknown) | null = null;
const restoreListeners = new Set<() => void>();
const emitRestore = () => restoreListeners.forEach((l) => l());

async function loadRestore(): Promise<boolean> {
  if (restoring === undefined) {
    const stored = (await storage.getItem(RESTORE_KEY, false as boolean)) === true;
    if (restoring === undefined) {
      restoring = stored;
      emitRestore();
    }
  }
  return restoring;
}

/** A returning driver just signed in: in memory at once (the root layout reads it in the same tick as the new
 *  token), then on disk. */
export async function beginRestore(): Promise<void> {
  restoring = true;
  patch = {};
  serverCopy = null;
  emitRestore();
  await storage.setItem(RESTORE_KEY, true);
}

/** Not a returning driver (a new account, a sign-out): nothing is waited for. */
export async function cancelRestore(): Promise<void> {
  const was = restoring;
  restoring = false;
  patch = {};
  if (was !== false) emitRestore();
  await storage.removeItem(RESTORE_KEY);
}

/** What to do once the first pull is answered: `found` false means the server has no profile for this account
 *  after all (src/state/afterSignIn.ts sends the driver through onboarding). Awaited before the pull resolves. */
export const setRestoreSettledHandler = (h: ((found: boolean) => unknown) | null) => {
  onSettled = h;
};

async function settleRestore(found: boolean): Promise<void> {
  restoring = false;
  patch = {};
  await storage.removeItem(RESTORE_KEY);
  try {
    await onSettled?.(found);
  } catch {
    // the flags are best effort; the profile itself is in
  }
  emitRestore();
}

export const restorePending = (): boolean => restoring === true;
/** The profile the server returned for the signed-in driver, or null (nothing yet, or it has none). */
export const serverProfile = (): TruckProfile | null => serverCopy;

/** undefined while the flag is being read, then whether a returning driver's profile is still on its way. */
export function useRestorePending(): boolean | undefined {
  const [, force] = useState(0);
  useEffect(() => {
    const l = () => force((n) => n + 1);
    restoreListeners.add(l);
    loadRestore();
    return () => {
      restoreListeners.delete(l);
    };
  }, []);
  return restoring;
}

/** The profile from the server, else the local one; the local copy is kept in step with what the server says.
 *  A change the server never got is sent first, so the older server copy does not undo it. */
export async function fetchProfile(): Promise<TruckProfile> {
  const local = await ensureTruckProfile();
  const restore = await loadRestore();
  try {
    if (!restore && (await storage.getItem<string | null>(UNSYNCED_KEY, null))) {
      await push(local);
      return getTruckProfile();
    }
    const before = edits;
    const resets = wipes;
    const { profile: remote, source } = await api.profile();
    if (wipes !== resets) return getTruckProfile(); // the phone changed hands while this was in flight
    serverCopy = remote;
    if (restoring) {
      // The server copy is the base; what the driver changed while it was on its way goes on top, and only then
      // is anything sent back.
      const edit = patch;
      const merged = { ...getTruckProfile(), ...remote, ...edit };
      await saveTruckProfile(merged);
      if (source === "vehicle" || remote.height_in !== DEFAULT_TRUCK.height_in || typeof edit.height_in === "number") confirmHeight();
      await settleRestore(true);
      if (Object.keys(edit).length) push(merged).catch(() => {});
      return merged;
    }
    if (edits !== before) return getTruckProfile(); // the driver saved while this was in flight: theirs is newer
    // A fleet's truck: mid-trip the phone's sizes are the trip's (a swapped trailer) and the truck's stored sizes
    // must not undo them; between trips the truck's come back (decision 2026-11-02, bug 2).
    if (source === "vehicle") confirmHeight(); // the fleet set this truck's sizes
    // The driver's own stored height that is not the silent default is an answer they gave once (the truck step,
    // the truck screen, by voice), on this phone or on the one they had before: a new phone does not ask again.
    // The default stays unconfirmed, because a driver who skipped the truck step has it stored too, and the map
    // must still ask them (src/truck/heightConfirmed.ts).
    else if (typeof remote.height_in === "number" && remote.height_in !== DEFAULT_TRUCK.height_in) confirmHeight();
    if (source === "vehicle" && midTrip()) return getTruckProfile();
    const cur = getTruckProfile();
    const merged = { ...cur, ...remote };
    if (stamp(merged) !== stamp(cur)) await saveTruckProfile(merged);
    return merged;
  } catch (e) {
    if (isMissing(e)) {
      serverCopy = null;
      // "Returning" by the server's word, yet it has no profile: a new driver after all.
      if (restoring) await settleRestore(false);
      return getTruckProfile();
    }
    throw e;
  }
}

/** Pulls the server's profile whenever the phone becomes signed in: on a cold start with a token, and right after a
 *  sign-in (a returning driver's truck is on the server, not on this phone). Not before: without a token the
 *  request is a 401 on every cold start. Signed out and in again, it runs again for the new identity. */
export function useProfileSync(signedIn: boolean) {
  useEffect(() => {
    if (signedIn) queryClient.prefetchQuery({ queryKey: PROFILE_KEY, queryFn: fetchProfile });
  }, [signedIn]);
  // A returning driver's truck that has not landed yet (no signal at sign-in, the driver went to the map anyway):
  // asked again on every return to the foreground and whenever the connection comes back, until it lands.
  useEffect(() => {
    if (!signedIn) return;
    const retry = () => {
      if (restorePending()) queryClient.prefetchQuery({ queryKey: PROFILE_KEY, queryFn: fetchProfile, staleTime: 0 });
    };
    const app = AppState.addEventListener("change", (s) => {
      if (s === "active") retry();
    });
    const net = useNetStore.subscribe((now, before) => {
      if (now.net === true && before.net !== true) retry();
    });
    return () => {
      app?.remove();
      net();
    };
  }, [signedIn]);
}

/** Every save of the truck: locally at once (the next request sees the new truck), then the server in the
 *  background; offline, the change waits behind UNSYNCED_KEY for the next start. */
export async function saveProfile(next: TruckProfile): Promise<void> {
  edits++;
  if (restoring) {
    // The server copy has not landed: remember what changed and send nothing (see the restore block above).
    const cur = getTruckProfile() as Record<string, unknown>;
    for (const [k, v] of Object.entries(next)) if (stamp(v) !== stamp(cur[k])) (patch as Record<string, unknown>)[k] = v;
    await saveTruckProfile(next);
    queryClient.setQueryData(PROFILE_KEY, next);
    return;
  }
  await saveTruckProfile(next);
  queryClient.setQueryData(PROFILE_KEY, next);
  push(next).catch(() => {});
}

/** "Delete my data": the default truck locally and nothing pending for the server (the server drops its copy
 *  with the rest of the driver's data). */
export async function resetProfile(): Promise<void> {
  edits++;
  wipes++;
  serverCopy = null;
  await cancelRestore();
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
