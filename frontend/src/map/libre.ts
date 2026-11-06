// Lazy loader for @maplibre/maplibre-react-native, same idea as src/keyboard.tsx for the keyboard
// controller: Expo Go does not ship the native module and a static import would throw at startup.
// The require runs inside a try, once, and only outside Expo Go / web; everything that needs
// MapLibre asks here and falls back when it gets null.

import { isRunningInExpoGo } from "expo";
import { Platform } from "react-native";

export type MapLibre = typeof import("@maplibre/maplibre-react-native");

// Expo Go 58 reports executionEnvironment "bare" like a dev build; the ExpoGo native module is the tell.
const inExpoGo = isRunningInExpoGo();

let cached: MapLibre | null | undefined;

export function loadMapLibre(): MapLibre | null {
  if (cached !== undefined) return cached;
  if (inExpoGo || Platform.OS === "web") {
    cached = null;
    return cached;
  }
  try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const m = require("@maplibre/maplibre-react-native") as Partial<MapLibre> | undefined;
    cached = m?.Map && m?.Camera && m?.OfflineManager ? (m as MapLibre) : null;
  } catch (e) {
    console.log(`[map] MapLibre not available: ${String(e)}`);
    cached = null;
  }
  return cached;
}
