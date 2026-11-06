// Driver settings that used to be five module stores in hooks/useTruckProfile.ts (hands-free, nudges, speed
// alerts, voice guidance, the "truck editor changed the size" flag): one zustand store, one document on disk
// (`settings_v2`; src/utils/storageMigrations.ts folds the old `*_v1` booleans into it).

import { create } from "zustand";

import { storage } from "../utils/storage";

export const SETTINGS_KEY = "settings_v2";

export type Settings = {
  /** Hands-free: the wake word where the platform has a detector (WAKE_SUPPORTED); on by default. */
  handsFree: boolean;
  /** Nudges while driving: break due, chains ahead, parking offers (on by default). */
  nudges: boolean;
  /** Speed limit alerts (off by default: posted limits come from map data and can be stale). */
  speedAlerts: boolean;
  /** Spoken turn-by-turn (on by default). */
  voiceGuidance: boolean;
};

export const DEFAULT_SETTINGS: Settings = { handsFree: true, nudges: true, speedAlerts: false, voiceGuidance: true };

type SettingsState = Settings & {
  loaded: boolean;
  /** The truck editor saved new dimensions: the map screen re-plans the active route once it is back in focus. */
  rerouteRequested: boolean;
  set: (patch: Partial<Settings>) => void;
  requestTruckReroute: () => void;
  takeTruckReroute: () => boolean;
  /** Tests only. */
  reset: () => void;
};

/** What the stored document decodes to; anything off-shape falls back to the defaults. */
export function parseSettings(raw: unknown): Settings {
  const out = { ...DEFAULT_SETTINGS };
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return out;
  for (const k of Object.keys(DEFAULT_SETTINGS) as (keyof Settings)[]) {
    const v = (raw as Record<string, unknown>)[k];
    if (typeof v === "boolean") out[k] = v;
  }
  return out;
}

export const useSettings = create<SettingsState>()((set, get) => ({
  ...DEFAULT_SETTINGS,
  loaded: false,
  rerouteRequested: false,
  set: (patch) => {
    for (const k of Object.keys(patch) as (keyof Settings)[]) touched.add(k);
    set(patch);
    const { handsFree, nudges, speedAlerts, voiceGuidance } = { ...get(), ...patch };
    storage.setItem(SETTINGS_KEY, { handsFree, nudges, speedAlerts, voiceGuidance });
  },
  requestTruckReroute: () => set({ rerouteRequested: true }),
  takeTruckReroute: () => {
    const r = get().rerouteRequested;
    if (r) set({ rerouteRequested: false });
    return r;
  },
  reset: () => set({ ...DEFAULT_SETTINGS, loaded: false, rerouteRequested: false }),
}));

let loading: Promise<void> | null = null;
/** Keys the driver set since start; the disk read never overwrites them (a value equal to the default is still a choice). */
const touched = new Set<keyof Settings>();

/** Reads the document once; a `set` made while the read is in flight wins over the disk. */
export function ensureSettings(): Promise<Settings> {
  if (!loading) {
    loading = storage
      .getItem<Record<string, unknown> | null>(SETTINGS_KEY, null)
      .then((raw) => {
        const parsed = parseSettings(raw);
        const patch: Partial<Settings> = {};
        for (const k of Object.keys(DEFAULT_SETTINGS) as (keyof Settings)[]) {
          if (!touched.has(k)) patch[k] = parsed[k];
        }
        useSettings.setState({ ...patch, loaded: true });
      })
      .catch(() => {
        useSettings.setState({ loaded: true });
      });
  }
  return loading.then(() => {
    const { handsFree, nudges, speedAlerts, voiceGuidance } = useSettings.getState();
    return { handsFree, nudges, speedAlerts, voiceGuidance };
  });
}

export const getSettings = (): Settings => {
  const { handsFree, nudges, speedAlerts, voiceGuidance } = useSettings.getState();
  return { handsFree, nudges, speedAlerts, voiceGuidance };
};

export const setHandsFree = (v: boolean) => useSettings.getState().set({ handsFree: v });
export const setNudgesEnabled = (v: boolean) => useSettings.getState().set({ nudges: v });
export const setSpeedAlerts = (v: boolean) => useSettings.getState().set({ speedAlerts: v });
export const setVoiceGuidance = (v: boolean) => useSettings.getState().set({ voiceGuidance: v });
export const requestTruckReroute = () => useSettings.getState().requestTruckReroute();
export const takeTruckReroute = () => useSettings.getState().takeTruckReroute();

export const useHandsFree = () => useSettings((s) => s.handsFree);
export const useNudgesEnabled = () => useSettings((s) => s.nudges);
export const useSpeedAlerts = () => useSettings((s) => s.speedAlerts);
export const useVoiceGuidance = () => useSettings((s) => s.voiceGuidance);

/** "Delete my data": the defaults, nothing on disk. */
export async function forgetSettings(): Promise<void> {
  touched.clear();
  loading = Promise.resolve();
  useSettings.setState({ ...DEFAULT_SETTINGS, loaded: true, rerouteRequested: false });
  await storage.removeItem(SETTINGS_KEY);
}

/** Tests only. */
export function __resetSettings() {
  loading = null;
  touched.clear();
  useSettings.getState().reset();
}
