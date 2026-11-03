// One version number for everything the app keeps in AsyncStorage, and the steps from one layout to the
// next. Runs once at startup (app/_layout.tsx) before any store reads its document.
//
// v1 (builds up to 2026-11-01): `active_route_v1` {active, savedAt}; `truck_profile_v1` as an object, or a
//     JSON string, or a JSON string holding a JSON string (older wrappers stringified twice); four booleans
//     `hands_free_v1`, `nudges_v1`, `speed_alerts_v1`, `voice_guidance_v1`.
// v2: `trip_v2` {active, savedAt, driving}; `truck_profile_v1` always a plain object; `settings_v2`
//     {handsFree, nudges, speedAlerts, voiceGuidance}.

import { storage } from "./storage";
import { parseStoredProfile } from "../hooks/useTruckProfile";
import { ACTIVE_ROUTE_KEY, decodeSaved } from "../state/routeStore";
import { DEFAULT_SETTINGS, SETTINGS_KEY, type Settings } from "../state/settingsStore";

export const SCHEMA_KEY = "storage_schema_version";
export const CURRENT_SCHEMA = 2;

const V1 = {
  route: "active_route_v1",
  profile: "truck_profile_v1",
  handsFree: "hands_free_v1",
  nudges: "nudges_v1",
  speedAlerts: "speed_alerts_v1",
  voiceGuidance: "voice_guidance_v1",
};

/** The old four booleans as one settings document (a missing flag keeps its default). */
export function settingsFromV1(flags: { handsFree?: unknown; nudges?: unknown; speedAlerts?: unknown; voiceGuidance?: unknown }): Settings {
  const pick = (v: unknown, d: boolean) => (typeof v === "boolean" ? v : d);
  return {
    handsFree: pick(flags.handsFree, DEFAULT_SETTINGS.handsFree),
    nudges: pick(flags.nudges, DEFAULT_SETTINGS.nudges),
    speedAlerts: pick(flags.speedAlerts, DEFAULT_SETTINGS.speedAlerts),
    voiceGuidance: pick(flags.voiceGuidance, DEFAULT_SETTINGS.voiceGuidance),
  };
}

/** storage.setItem reports a failed write as `false` instead of throwing; a step must stop before it removes
 *  the old key it could not copy. */
async function write(key: string, value: Record<string, unknown> | number): Promise<void> {
  if (!(await storage.setItem(key, value))) throw new Error(`storage write failed: ${key}`);
}

async function v1ToV2(): Promise<void> {
  // the saved route: same document plus `driving: false` (v1 never knew it), under the new key
  const rawRoute = await storage.getItem<Record<string, unknown> | string | null>(V1.route, null);
  const saved = decodeSaved(rawRoute);
  if (saved) await write(ACTIVE_ROUTE_KEY, { active: saved.active, savedAt: saved.savedAt, driving: false } as unknown as Record<string, unknown>);
  await storage.removeItem(V1.route);
  // the truck profile: whatever encoding it had, a plain object from here on
  const rawProfile = await storage.getItem<Record<string, unknown> | string | null>(V1.profile, null);
  const profile = parseStoredProfile(rawProfile);
  if (profile) await write(V1.profile, profile as Record<string, unknown>);
  // the four booleans into one document
  const [handsFree, nudges, speedAlerts, voiceGuidance] = await Promise.all([
    storage.getItem<boolean | null>(V1.handsFree, null),
    storage.getItem<boolean | null>(V1.nudges, null),
    storage.getItem<boolean | null>(V1.speedAlerts, null),
    storage.getItem<boolean | null>(V1.voiceGuidance, null),
  ]);
  await write(SETTINGS_KEY, settingsFromV1({ handsFree, nudges, speedAlerts, voiceGuidance }));
  await Promise.all([V1.handsFree, V1.nudges, V1.speedAlerts, V1.voiceGuidance].map((k) => storage.removeItem(k)));
}

const STEPS: Record<number, () => Promise<void>> = { 1: v1ToV2 };

/** Brings the storage to CURRENT_SCHEMA; a fresh install just gets the version stamp. Never throws. */
export async function migrateStorage(): Promise<number> {
  try {
    let version = (await storage.getItem<number | null>(SCHEMA_KEY, null)) ?? 1;
    if (typeof version !== "number" || version < 1) version = 1;
    while (version < CURRENT_SCHEMA) {
      const step = STEPS[version];
      if (step) await step();
      version += 1;
      await write(SCHEMA_KEY, version);
    }
    return version;
  } catch {
    return CURRENT_SCHEMA; // a failed step leaves the old keys in place; the stores fall back to their defaults
  }
}
