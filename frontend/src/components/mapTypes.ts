import type { ActiveRoute, LatLng, Place } from "../types";

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
};
