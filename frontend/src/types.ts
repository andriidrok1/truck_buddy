export type LatLng = { lat: number; lng: number };

export type Category = "truck_stop" | "hotel" | "rest_area";

export type Place = {
  id: string;
  name: string;
  category: Category;
  lat: number;
  lng: number;
  tags: string[];
  address?: string | null;
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
};

export type PlacesResponse = {
  category: Category;
  radius_km: number;
  widened: boolean;
  places: Place[];
  source: string;
};

export type AgentResponse = {
  status: "route_set" | "chat" | "no_results" | "not_understood";
  transcript: string;
  category?: Category | null;
  reply: string;
  message?: string | null;
  place?: Place | null;
  route?: RouteInfo | null;
  audio_url?: string | null;
  source: string;
};

export type ActiveRoute = { place: Place; route: RouteInfo };

export type Phase = "ready" | "listening" | "thinking" | "navigating";
