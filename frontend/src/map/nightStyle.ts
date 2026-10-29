// Google Maps night style for the dark theme (react-native-maps `customMapStyle`): dark land and water,
// muted labels, roads a step lighter than the land so the blue route line stays the brightest thing.
import type { MapStyleElement } from "react-native-maps";

export const NIGHT_MAP_STYLE: MapStyleElement[] = [
  { elementType: "geometry", stylers: [{ color: "#1d2733" }] },
  { elementType: "labels.text.fill", stylers: [{ color: "#8a94a6" }] },
  { elementType: "labels.text.stroke", stylers: [{ color: "#1d2733" }] },
  { elementType: "labels.icon", stylers: [{ visibility: "off" }] },
  { featureType: "administrative.locality", elementType: "labels.text.fill", stylers: [{ color: "#b8c0cc" }] },
  { featureType: "poi", stylers: [{ visibility: "off" }] },
  { featureType: "poi.park", elementType: "geometry", stylers: [{ color: "#1f3328" }] },
  { featureType: "road", elementType: "geometry", stylers: [{ color: "#2e3a4a" }] },
  { featureType: "road", elementType: "geometry.stroke", stylers: [{ color: "#1a222d" }] },
  { featureType: "road", elementType: "labels.text.fill", stylers: [{ color: "#9aa3b2" }] },
  { featureType: "road.highway", elementType: "geometry", stylers: [{ color: "#4a5568" }] },
  { featureType: "road.highway", elementType: "geometry.stroke", stylers: [{ color: "#2a3340" }] },
  { featureType: "road.highway", elementType: "labels.text.fill", stylers: [{ color: "#c9d1dc" }] },
  { featureType: "transit", stylers: [{ visibility: "off" }] },
  { featureType: "water", elementType: "geometry", stylers: [{ color: "#0f1a2a" }] },
  { featureType: "water", elementType: "labels.text.fill", stylers: [{ color: "#4f6480" }] },
];
