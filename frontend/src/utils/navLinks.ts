import { Linking } from "react-native";

/** Google Maps Street View at a point (opens the app when installed, else the browser). */
export function openStreetView(lat: number, lng: number) {
  return Linking.openURL(`https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=${lat},${lng}`);
}
