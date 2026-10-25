export type LatLng = { lat: number; lng: number };

export type Category = "truck_stop" | "hotel" | "rest_area" | "destination";

export type Place = {
  id: string;
  name: string;
  category: Category;
  lat: number;
  lng: number;
  tags: string[];
  address?: string | null;
  rating?: number | null;
  rating_count?: number | null;
  summary?: string | null;
  distance_km?: number | null;
  distance_mi?: number | null;
};

export type RouteInfo = {
  geometry: [number, number][];
  distance_m: number;
  duration_s: number;
  distance_mi: number;
  duration_min: number;
  summary: string;
  source: string;
  notices?: string[];
  truck_aware?: boolean;
};

export type PlacesResponse = {
  category: Category;
  radius_km: number;
  widened: boolean;
  places: Place[];
  source: string;
};

export type ParkingSpot = { kind: "entry" | "park"; x: number; y: number; lat: number; lng: number };

export type ParkingCheck = {
  verdict: "listed" | "likely" | "unlikely" | "unclear";
  label: string;
  reason: string;
  source: "place_data" | "satellite" | "none";
  image_path?: string | null;
  entry_tip?: string | null;
  park_tip?: string | null;
  spots?: ParkingSpot[];
};

export type AgentResponse = {
  status: "options" | "route_set" | "chat" | "no_results" | "not_understood";
  transcript: string;
  category?: Category | null;
  reply: string;
  message?: string | null;
  place?: Place | null;
  route?: RouteInfo | null;
  parking?: ParkingCheck | null;
  options?: Place[];
  audio_url?: string | null;
  source: string;
};

export type ActiveRoute = { place: Place; route: RouteInfo; parking?: ParkingCheck | null };

export type Phase = "ready" | "listening" | "thinking" | "options" | "navigating";

export type ParkingState = { loading: boolean; result?: ParkingCheck };
