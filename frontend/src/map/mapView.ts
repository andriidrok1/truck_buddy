// The map's three looks (Figma "Weather v2", W2): plain map, satellite imagery, weather (radar over a muted map).
// Pure helpers, no React Native imports, so they run in plain jest.

import type { MapStyleElement } from "react-native-maps";

import type { MapView } from "../state/settingsStore";

export type RadarFrame = {
  enabled: boolean;
  /** ISO time of the live radar frame. */
  time?: string;
  /** Our id of the live layer, e.g. "n0q-202611120010"; null while the backend has no frame yet. */
  live?: string | null;
  /** When the HRRR run behind the forecast layers started; layer "hrrr-f{minute}" shows this + minute. */
  forecast_init?: string | null;
  forecast_step_min?: number;
  forecast_max_min?: number;
  max_zoom?: number;
  /** "/api/radar/tiles/{layer}/{z}/{x}/{y}.png?k=..." with {layer} still to fill. */
  tile_url?: string | null;
};

/** What the map layers draw for the radar: one tile template, and a key that changes with the frame (so the
 *  map's tile layer is remounted instead of showing cached tiles of an old frame). */
export type RadarOverlay = { urlTemplate: string; key: string; maxNativeZoom: number; /** Overrides the theme's radar opacity (offline: faint). */ opacity?: number };

export const RADAR_OPACITY = { light: 0.75, dark: 0.6 } as const;

/** react-native-maps `mapType` for a look. Android has no "mutedStandard": the weather view there is a standard
 *  map with WEATHER_MAP_STYLE. */
export function nativeMapType(view: MapView, os: string): "standard" | "satellite" | "mutedStandard" {
  if (view === "satellite") return "satellite";
  if (view === "weather" && os === "ios") return "mutedStandard";
  return "standard";
}

/** The radar tiles for a layer of a frame, as an absolute URL template the map's tile layer can load. */
export function radarOverlay(frame: RadarFrame | null, origin: string, layer?: string | null): RadarOverlay | null {
  const id = layer ?? frame?.live;
  if (!frame?.enabled || !frame.tile_url || !id) return null;
  // A forecast layer's name is the same for every HRRR run: the run goes into the URL and the key, so neither
  // the phone's tile cache nor the map's tile layer keeps showing the last run's weather.
  const run = id.startsWith("hrrr") && frame.forecast_init ? frame.forecast_init : null;
  return {
    urlTemplate: origin + frame.tile_url.replace("{layer}", encodeURIComponent(id)) + (run ? `&run=${encodeURIComponent(run)}` : ""),
    key: run ? `${id}@${run}` : id,
    maxNativeZoom: frame.max_zoom ?? 10,
  };
}

/** "1:50 PM" for the radar chip, in the phone's time zone. */
export function frameClock(iso: string | undefined, locale?: string): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleTimeString(locale, { hour: "numeric", minute: "2-digit" });
}

/** Light, muted Google style for the weather view on Android: grey land, quiet roads, no POIs, so the radar and
 *  the blue route are what you see. */
export const WEATHER_MAP_STYLE: MapStyleElement[] = [
  { elementType: "geometry", stylers: [{ color: "#eef0f2" }] },
  { elementType: "labels.icon", stylers: [{ visibility: "off" }] },
  { elementType: "labels.text.fill", stylers: [{ color: "#7a8089" }] },
  { elementType: "labels.text.stroke", stylers: [{ color: "#f5f6f7" }] },
  { featureType: "administrative", elementType: "geometry.stroke", stylers: [{ color: "#c9ccd1" }] },
  { featureType: "poi", stylers: [{ visibility: "off" }] },
  { featureType: "road", elementType: "geometry", stylers: [{ color: "#ffffff" }] },
  { featureType: "road", elementType: "labels", stylers: [{ visibility: "off" }] },
  { featureType: "road.highway", elementType: "geometry", stylers: [{ color: "#dfe2e6" }] },
  { featureType: "road.highway", elementType: "labels", stylers: [{ visibility: "on" }] },
  { featureType: "transit", stylers: [{ visibility: "off" }] },
  { featureType: "water", elementType: "geometry", stylers: [{ color: "#d6dde5" }] },
];
