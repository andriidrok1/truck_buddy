import { Linking, Platform } from "react-native";

async function openWithFallback(appUrl: string | null, webUrl: string) {
  if (appUrl) {
    try {
      await Linking.openURL(appUrl);
      return;
    } catch {
      // app not installed -> web
    }
  }
  await Linking.openURL(webUrl);
}

export function openGoogleMaps(lat: number, lng: number) {
  const web = `https://www.google.com/maps/dir/?api=1&destination=${lat},${lng}&travelmode=driving`;
  const app =
    Platform.OS === "ios"
      ? `comgooglemaps://?daddr=${lat},${lng}&directionsmode=driving`
      : Platform.OS === "android"
        ? `google.navigation:q=${lat},${lng}`
        : null;
  return openWithFallback(app, web);
}

export function openWaze(lat: number, lng: number) {
  const web = `https://waze.com/ul?ll=${lat},${lng}&navigate=yes`;
  const app = Platform.OS === "web" ? null : `waze://?ll=${lat},${lng}&navigate=yes`;
  return openWithFallback(app, web);
}
