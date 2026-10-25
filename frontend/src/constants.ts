import type { Category, LatLng } from "./types";
import type { ThemeColors } from "./theme";

export const DEMO_LOCATION: LatLng = { lat: 38.5816, lng: -121.4944 }; // Sacramento, CA

export const TRUCK_PROFILE = { height: "13 ft 6 in", trailer_length: "53 ft trailer" };
export const TRUCK_PROFILE_LABEL = `${TRUCK_PROFILE.height} · ${TRUCK_PROFILE.trailer_length}`;

export const CATEGORIES: { key: Category; label: string; icon: string; testID: string }[] = [
  { key: "truck_stop", label: "Truck stops", icon: "gas-station", testID: "chip-truck-stops" },
  { key: "hotel", label: "Hotels", icon: "bed", testID: "chip-hotels" },
  { key: "rest_area", label: "Rest areas", icon: "pine-tree-variant", testID: "chip-rest-areas" },
];

export const CATEGORY_ICON: Record<Category, string> = {
  truck_stop: "gas-station",
  hotel: "bed",
  rest_area: "pine-tree-variant",
};

export function categoryColor(c: ThemeColors, cat: Category) {
  return cat === "truck_stop" ? c.truckStop : cat === "hotel" ? c.hotel : c.restArea;
}

export const MESSAGES = {
  notUnderstood: "Sorry, I didn't catch that. Try again.",
  noPlaces: "No places found nearby",
  micNeeded: "Microphone access is needed to talk to your copilot",
  placesUnavailable: "Places are unavailable right now. Try again.",
};
