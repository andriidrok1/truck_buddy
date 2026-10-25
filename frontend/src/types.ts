export type LatLng = { lat: number; lng: number };

export type Category = "truck_stop" | "hotel" | "rest_area" | "destination" | "service";

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

export type Maneuver = {
  action: string;
  direction?: string | null;
  instruction: string;
  lat: number;
  lng: number;
  index: number;
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
  traffic?: { level: "slow" | "jam"; points: [number, number][] }[];
  maneuvers?: Maneuver[];
  approach?: Approach | null;
};

export type Approach = { bearing: number; compass: string; street?: string | null };

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
  relative?: boolean;
};

export type PriceFact = {
  what: string;
  amount_usd_low: number;
  amount_usd_high: number;
  source: "review" | "website";
  quote: string;
};

export type ProviderFacts = {
  mobile_service: boolean | "unknown";
  hours_24_7: boolean | "unknown";
  truck_focused: boolean;
  prices: PriceFact[];
  wait_time?: { typical_min_low: number; typical_min_high: number; quote: string; source?: "review" | "website" } | null;
  payment: string[];
  red_flags: string[];
  praise: string[];
  last_review_date?: string | null;
};

export type Provider = {
  id: string;
  name: string;
  lat: number;
  lng: number;
  phone?: string | null;
  website?: string | null;
  address?: string | null;
  rating?: number | null;
  rating_count: number;
  distance_mi?: number | null;
  open_now?: boolean | null;
  listed_24_7: boolean;
  shop_only: boolean;
  score: number;
  source: string;
  facts: ProviderFacts;
  quotes: string[];
};

export type CallCard = {
  problem: string;
  tire?: string | null;
  location?: string | null;
  gps: string;
  map_link: string;
  truck: string;
  questions: string[];
  share_text: string;
};

export type ProvidersResult = {
  providers: Provider[];
  call_card?: CallCard | null;
  summary?: string | null;
  provider_source?: string | null;
};

export type AgentResponse = {
  status: "options" | "route_set" | "chat" | "no_results" | "not_understood" | "researching" | "providers";
  research_id?: string | null;
  providers?: Provider[];
  call_card?: CallCard | null;
  summary?: string | null;
  provider_source?: string | null;
  dial_phone?: string | null;
  transcript: string;
  category?: Category | null;
  reply: string;
  message?: string | null;
  place?: Place | null;
  route?: RouteInfo | null;
  parking?: ParkingCheck | null;
  options?: Place[];
  audio_url?: string | null;
  audio_url_2?: string | null;
  timings?: Record<string, number>;
  end_conversation?: boolean;
  actions?: AppAction[];
  source: string;
};

export type AppAction = { type: string; value?: boolean | number | string | null };

export type UiState = { driving: boolean; voice_guidance: boolean; hands_free: boolean; options_count: number };

export type ActiveRoute = { place: Place; route: RouteInfo; parking?: ParkingCheck | null };

export type Phase = "ready" | "listening" | "thinking" | "options" | "navigating" | "providers";

export type ParkingState = { loading: boolean; result?: ParkingCheck };
