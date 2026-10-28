// MapLibre's native SDK only enters the build with EXPO_PUBLIC_MAP_PROVIDER=maplibre (its config
// plugin is added under the same variable in app.config.ts). Without it, autolinking would still
// compile the MapLibre bridge against a missing SDK and fail the iOS archive, so the bridge is
// unlinked too. The JS side already falls back to react-native-maps (src/map/libre.ts).
const mapLibre = process.env.EXPO_PUBLIC_MAP_PROVIDER?.trim().toLowerCase() === "maplibre";

module.exports = {
  dependencies: mapLibre
    ? {}
    : { "@maplibre/maplibre-react-native": { platforms: { ios: null, android: null } } },
};
