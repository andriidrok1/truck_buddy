import type { Category, LatLng } from "./types";
import type { ThemeColors } from "./theme";

export const DEMO_LOCATION: LatLng = { lat: 38.5816, lng: -121.4944 }; // Sacramento, CA

export const CATEGORIES: { key: Category; label: string; icon: string; testID: string }[] = [
  { key: "truck_stop", label: "Truck stops", icon: "gas-station", testID: "chip-truck-stops" },
  { key: "hotel", label: "Hotels", icon: "bed", testID: "chip-hotels" },
  { key: "rest_area", label: "Rest areas", icon: "pine-tree-variant", testID: "chip-rest-areas" },
];

// MaterialDesignIcons names; tsc fails here when a Category has no icon.
export const CATEGORY_ICON: Record<Category, string> = {
  truck_stop: "gas-station",
  hotel: "bed",
  rest_area: "pine-tree-variant",
  destination: "flag-checkered",
  service: "wrench",
  walmart: "store",
  cat_scale: "scale",
  truck_wash: "car-wash",
  weigh_station: "police-badge",
};

const FALLBACK_ICON = "map-marker";

/** Icon for a category; a value the backend added after this build still renders (generic marker). */
export function categoryIcon(cat: Category | string): string {
  return (CATEGORY_ICON as Record<string, string>)[cat] ?? FALLBACK_ICON;
}

const CATEGORY_COLOR: Record<Category, (c: ThemeColors) => string> = {
  truck_stop: (c) => c.truckStop,
  hotel: (c) => c.hotel,
  rest_area: (c) => c.restArea,
  destination: (c) => c.brandPrimary,
  service: (c) => c.brandPrimary,
  walmart: (c) => c.walmart,
  cat_scale: (c) => c.catScale,
  truck_wash: (c) => c.truckWash,
  weigh_station: (c) => c.weighStation,
};

/** Pin / chip tint for a category; unknown runtime values get the brand colour. */
export function categoryColor(c: ThemeColors, cat: Category | string) {
  const pick = (CATEGORY_COLOR as Record<string, (c: ThemeColors) => string>)[cat];
  return pick ? pick(c) : c.brandPrimary;
}

export const MESSAGES = {
  notUnderstood: "Sorry, I didn't catch that. Try again.",
  noPlaces: "No places found nearby",
  micNeeded: "Microphone access is needed to talk to your copilot",
  placesUnavailable: "Places are unavailable right now. Try again.",
};

export const PRIVACY_URL = "https://truckbuddy.io/privacy";
