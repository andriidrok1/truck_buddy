// The main map: react-native-maps (Apple Maps on iOS, Google on Android) by default, MapLibre
// with OpenFreeMap tiles and offline packs when the build was made with
// EXPO_PUBLIC_MAP_PROVIDER=maplibre and its native module loaded (src/map/provider.ts).
// Both layers take the same props and expose the same handle.

import { forwardRef } from "react";

import { MAP_PROVIDER } from "../map/provider";
import MapLayerApple from "./MapLayerApple.native";
import MapLayerLibre from "./MapLayerLibre.native";
import type { MapLayerHandle, MapLayerProps } from "./mapTypes";

export { ViewMarker } from "./MapLayerApple.native";

const MapLayer = forwardRef<MapLayerHandle, MapLayerProps>(function MapLayer(props, ref) {
  return MAP_PROVIDER === "maplibre" ? <MapLayerLibre ref={ref} {...props} /> : <MapLayerApple ref={ref} {...props} />;
});

export default MapLayer;
