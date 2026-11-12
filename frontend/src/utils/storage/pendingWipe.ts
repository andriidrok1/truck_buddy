// "Delete my data" removes every key below. A removal can fail (the storage helpers report it as `false`, they
// never throw); then the old language or consent would come back on the next cold start while memory said first
// launch. So the wipe raises WIPE_PENDING_KEY first and lowers it only when every key is gone; while it is up, the
// first read after a cold start finishes the wipe before anything is read or written (every storage call
// awaits `wipeGate`). src/__tests__/deleteMyData.test.ts checks this list against what the app actually writes.

import AsyncStorage from "@react-native-async-storage/async-storage";

export const WIPE_PENDING_KEY = "wipe_pending_v1";

/** AsyncStorage keys the app writes. Not here: the schema stamp (a fresh install writes it too). */
export const APP_KEYS = [
  "ai_consent_v2",
  "onboarded_v1",
  "lang_v1",
  "intro_seen_v1",
  "height_confirmed_v1",
  "how_to_seen_v1",
  "theme_pref_v1",
  "truck_profile_v1",
  "profile_unsynced_v1",
  "settings_v2",
  "trip_v2",
  "trip_arrived_v1",
  "trip_events_pending",
] as const;

/** Keychain keys (AsyncStorage on web). */
export const SECURE_KEYS = ["auth_token", "auth_role", "auth_via", "auth_refresh", "eld_token_v1"] as const;

type Remove = (key: string) => Promise<unknown>;

async function tryRemove(remove: Remove, key: string): Promise<boolean> {
  try {
    await remove(key);
    return true;
  } catch {
    return false;
  }
}

const ALL_KEYS: string[] = [...APP_KEYS, ...SECURE_KEYS];
const isSecure = (k: string) => (SECURE_KEYS as readonly string[]).includes(k);

/** The flag holds what is still to remove: "*" while a wipe runs (a crash midway leaves everything to do), then
 *  the list of keys that would not go. */
const flagKeys = (raw: string | null): string[] | null => {
  if (raw === null) return null;
  try {
    const v = JSON.parse(raw) as unknown;
    if (Array.isArray(v)) return v.filter((k): k is string => typeof k === "string" && ALL_KEYS.includes(k));
  } catch {
    // an older or broken flag: treat as a whole wipe
  }
  return ALL_KEYS;
};

async function setFlag(keys: string[] | "*"): Promise<void> {
  try {
    await AsyncStorage.setItem(WIPE_PENDING_KEY, JSON.stringify(keys));
  } catch {
    // nothing to fall back on
  }
}

/**
 * Removes the given app keys (all by default), retrying a failed one once. Lowers the pending flag when nothing is
 * left, else leaves the leftovers in it. Returns the keys that are still there (empty = done).
 */
export async function removeAppKeys(removeSecure: Remove, keys: string[] = ALL_KEYS): Promise<string[]> {
  writtenSinceWipe.clear(); // what was written before this pass (the wipe's own steps) is old; from here on, new
  const removeOf = (k: string): Remove => (isSecure(k) ? removeSecure : (key) => AsyncStorage.removeItem(key));
  let left = keys;
  for (let attempt = 0; attempt < 2 && left.length; attempt++) {
    const ok = await Promise.all(left.map((k) => tryRemove(removeOf(k), k)));
    left = left.filter((_, i) => !ok[i]);
  }
  if (left.length) {
    console.warn(`[storage] wipe left ${left.join(", ")}; finishing on the next start or sign-in`);
    await setFlag(left);
    return left;
  }
  await tryRemove((key) => AsyncStorage.removeItem(key), WIPE_PENDING_KEY);
  return [];
}

/** Keys written since the last removal pass: they hold the new identity's choices (language, consent, the new token). */
const writtenSinceWipe = new Set<string>();
/** Called by the storage helpers on every write. */
export const noteWrite = (key: string) => writtenSinceWipe.add(key);

export async function markWipePending(): Promise<void> {
  writtenSinceWipe.clear();
  await setFlag("*");
}

/**
 * The phone signed in again in the same run with the flag still up: the leftovers belong to the old identity, so
 * they go now, except what the new run has written since (re-review of #129: otherwise the next cold start would
 * wipe the new identity). The flag comes down with them.
 */
export async function settleWipeAfterSignIn(removeSecure: Remove): Promise<string[]> {
  let keys: string[] | null;
  try {
    keys = flagKeys(await AsyncStorage.getItem(WIPE_PENDING_KEY));
  } catch {
    return [];
  }
  if (keys === null) return [];
  return removeAppKeys(removeSecure, keys.filter((k) => !writtenSinceWipe.has(k)));
}

let gate: Promise<void> | null = null;
let gateDone = false;

/**
 * Once per process: a wipe left unfinished by the last run is finished before the first storage call. Every call
 * (reads and writes) waits on the same promise, so their order is kept; once it has settled this is null and the
 * calls go straight through, with no extra tick.
 */
export function wipeGate(removeSecure: Remove): Promise<void> | null {
  if (gateDone) return null;
  if (!gate) {
    gate = (async () => {
      let keys: string[] | null;
      try {
        keys = flagKeys(await AsyncStorage.getItem(WIPE_PENDING_KEY));
      } catch {
        return;
      }
      if (keys !== null) await removeAppKeys(removeSecure, keys); // nothing has been written in this run yet
    })().finally(() => {
      gateDone = true;
    });
  }
  return gate;
}

/** Tests only. */
export function __resetWipeGate() {
  gate = null;
  gateDone = false;
  writtenSinceWipe.clear();
}
