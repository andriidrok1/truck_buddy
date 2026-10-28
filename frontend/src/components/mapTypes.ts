import type { ActiveRoute, LatLng, ParkingSpot, Place } from "../types";

/** Same Esri World Imagery the satellite parking check is made on. */
export const ESRI_TILES = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}";

/** Half-span (degrees) of the ~300 m x 300 m parking check frame, matching the backend. */
export function frameSpan(lat: number) {
  const dLat = 150 / 111320;
  return { dLat, dLng: dLat / Math.max(0.2, Math.cos((lat * Math.PI) / 180)) };
}

/** Esri World Imagery is served to this zoom; above it react-native-maps upscales the z19 tiles. */
export const ESRI_MAX_NATIVE_Z = 19;
/** Camera limits of the parking view: close enough to read a lot, never out to the whole state. */
export const SAT_MAX_Z = 21;
export const SAT_MIN_Z = 14;

/**
 * Apple Maps camera distance (m, `MKMapView.cameraZoomRange`) that shows web-mercator zoom `zoom`
 * in a view `viewHeightPx` tall, assuming MapKit's ~30 degree vertical field of view. Apple Maps
 * ignores `maxZoomLevel`/`minZoomLevel` as a hard cap (it only bounces back after the gesture),
 * so the parking view passes these distances too.
 */
export function cameraDistanceForZoom(zoom: number, lat: number, viewHeightPx: number) {
  const metersPerPx = (156543.03392 * Math.cos((lat * Math.PI) / 180)) / 2 ** zoom;
  return (viewHeightPx * metersPerPx) / 2 / Math.tan((15 * Math.PI) / 180);
}

export type ParkingSatMapProps = {
  center: LatLng;
  place?: Place | null;
  spots: ParkingSpot[];
  /** Last ~300 m of the active route, [lat, lng] pairs. */
  tail: [number, number][];
};

export type MapLayerHandle = { recenter: () => void; zoomBy: (delta: number) => void };

export type MapLayerProps = {
  driver: LatLng;
  heading: number | null;
  places: Place[];
  active: ActiveRoute | null;
  /** Space covered by the top overlay / bottom sheet, so fitting keeps content visible. */
  topInset: number;
  bottomInset: number;
  /** Changing this key fits the camera to `fitPoints`. */
  fitKey: string;
  fitPoints: LatLng[];
  onCenterChange: (c: LatLng) => void;
  onPlacePress: (p: Place) => void;
  /** Driving mode: keep the camera close behind the driver. */
  follow?: boolean;
  /** The driver dragged the map (stops following). */
  onUserPan?: () => void;
  /** Any touch on the map (tap or drag); the screen uses it to put the keyboard away. */
  onMapTouch?: () => void;
};
