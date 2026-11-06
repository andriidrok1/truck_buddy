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
  "height_confirmed_v1",
  "theme_pref_v1",
  "truck_profile_v1",
  "profile_unsynced_v1",
  "settings_v2",
  "trip_v2",
  "trip_arrived_v1",
  "trip_events_pending",
] as const;

/** Keychain keys (AsyncStorage on web). */
export const SECURE_KEYS = ["auth_token", "auth_role", "auth_via", "eld_token_v1"] as const;

type Remove = (key: string) => Promise<unknown>;

async function tryRemove(remove: Remove, key: string): Promise<boolean> {
  try {
    await remove(key);
    return true;
  } catch {
    return false;
  }
}

/**
 * Removes every app key, retrying a failed one once, and lowers the pending flag when nothing is left.
 * Returns the keys that are still there (empty = done).
 */
export async function removeAppKeys(removeSecure: Remove): Promise<string[]> {
  const jobs: [Remove, string][] = [
    ...APP_KEYS.map((k): [Remove, string] => [(key) => AsyncStorage.removeItem(key), k]),
    ...SECURE_KEYS.map((k): [Remove, string] => [removeSecure, k]),
  ];
  let left = jobs;
  for (let attempt = 0; attempt < 2 && left.length; attempt++) {
    const ok = await Promise.all(left.map(([rm, k]) => tryRemove(rm, k)));
    left = left.filter((_, i) => !ok[i]);
  }
  if (left.length) {
    console.warn(`[storage] wipe left ${left.map(([, k]) => k).join(", ")}; finishing on the next start`);
    return left.map(([, k]) => k);
  }
  await tryRemove((key) => AsyncStorage.removeItem(key), WIPE_PENDING_KEY);
  return [];
}

export async function markWipePending(): Promise<void> {
  try {
    await AsyncStorage.setItem(WIPE_PENDING_KEY, "true");
  } catch {
    // nothing to fall back on: the wipe below still runs
  }
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
      try {
        if ((await AsyncStorage.getItem(WIPE_PENDING_KEY)) === null) return;
      } catch {
        return;
      }
      await removeAppKeys(removeSecure);
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
}
