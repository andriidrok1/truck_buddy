// Which map draws the main screen. "apple" is react-native-maps (Apple Maps on iOS, Google on
// Android): the default and the only one Expo Go can run. "maplibre" is MapLibre with
// OpenFreeMap vector tiles and offline corridor packs; it needs a dev build made with
// EXPO_PUBLIC_MAP_PROVIDER=maplibre (the config plugin is added under the same variable).

import { loadMapLibre } from "./libre";

export type MapProvider = "apple" | "maplibre";

/** The provider the build asked for (env), or "apple" when it said nothing or something else. */
export function requestedProvider(env: string | undefined): MapProvider {
  return env?.trim().toLowerCase() === "maplibre" ? "maplibre" : "apple";
}

/** MapLibre only when the build asked for it AND its native module loaded; otherwise Apple. */
export function resolveProvider(env: string | undefined, mapLibreLoaded: boolean): MapProvider {
  return requestedProvider(env) === "maplibre" && mapLibreLoaded ? "maplibre" : "apple";
}

export const REQUESTED_PROVIDER: MapProvider = requestedProvider(process.env.EXPO_PUBLIC_MAP_PROVIDER);
export const MAP_PROVIDER: MapProvider = resolveProvider(process.env.EXPO_PUBLIC_MAP_PROVIDER, loadMapLibre() !== null);

/** OpenFreeMap styles (free, no key). The dark one is used when the theme is dark. */
export const OPENFREEMAP_STYLE = "https://tiles.openfreemap.org/styles/liberty";
export const OPENFREEMAP_DARK_STYLE = "https://tiles.openfreemap.org/styles/dark";
/** Required on screen whenever the OpenFreeMap style is shown. */
export const OPENFREEMAP_ATTRIBUTION = "© OpenFreeMap © OpenMapTiles © OpenStreetMap contributors";
