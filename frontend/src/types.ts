export type LatLng = { lat: number; lng: number };

export type Category =
  | "truck_stop"
  | "hotel"
  | "rest_area"
  | "destination"
  | "service"
  | "walmart"
  | "cat_scale"
  | "truck_wash"
  | "weigh_station";

/** One Google review. Google's terms: show the author and link to the review whenever it is displayed. */
export type Review = {
  author: string;
  rating?: number | null;
  text: string;
  uri?: string | null;
  time?: string | null;
};

export type ParkingTrend = "FILLING" | "CLEARING" | "STEADY";

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
  diesel_price?: number | null;
  diesel_kind?: "truck" | "auto" | null;
  diesel_updated?: string | null;
  distance_km?: number | null;
  distance_mi?: number | null;
  /** Up to 5 Google reviews. */
  reviews?: Review[];
  /** Live truck-parking counts (TPIMS / NTAD) for truck stops and rest areas. */
  parking_available?: number | null;
  parking_capacity?: number | null;
  parking_low?: boolean | null;
  parking_trend?: ParkingTrend | null;
  /** RFC 3339 timestamp of the last count. */
  parking_updated?: string | null;
  parking_source?: "tpims" | "ntad" | null;
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
  // Everything below is optional: older backends (and the fixtures) do not send it.
  /** Server-side route handle, sent back on /api/reroute so the engine can resume this plan. */
  handle?: string;
  /** Posted limit from geometry index `index` until the next entry. */
  speed_limits?: SpeedLimit[];
  incidents?: Incident[];
  weather?: WeatherAlert[];
  chain_controls?: ChainControl[];
  labels?: string[];
  tolls_usd?: number | null;
  alternatives?: RouteInfo[];
  /** Via stops the driver asked for, echoed back on a reroute. */
  via?: [number, number][];
  /** Geometry index where each route section (between via stops) starts. */
  section_starts?: number[];
};

export type SpeedLimit = { index: number; mph: number };

export type Incident = { type: string; closed: boolean; description: string; index: number; criticality: string };

export type WeatherAlert = {
  id: string;
  event: string;
  severity: string;
  headline: string;
  area: string;
  index: number;
  ends?: string | null;
};

export type ChainControl = {
  id: string;
  route: string;
  location: string;
  status: string;
  index: number;
  lat: number;
  lng: number;
  description?: string;
};

/** The first active Caltrans chain control on the route ahead (NudgeRequest.chain_control_ahead). */
export type ChainControlAhead = { route: string; location: string; status: "R-1" | "R-2" | "R-3"; miles_ahead: number };

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
  /** Truck-capable lot elsewhere in the frame (only with verdict "unlikely"). */
  nearby?: { direction: string; kind: "public lot" | "private yard" | "unclear" } | null;
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
  /** `confirm` = a yes/no question about what was heard ("Did you say Reno, NV?"): the mic reopens after it. */
  status: "options" | "route_set" | "chat" | "confirm" | "no_results" | "not_understood" | "researching" | "providers" | "error";
  /** What the classifier decided (logs, the tester screen). */
  intent?: string | null;
  /** The text behind audio_url and audio_url_2, in order; the playback timeout is sized from it. */
  reply_parts?: string[];
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

export type ActiveRoute = {
  place: Place;
  route: RouteInfo;
  parking?: ParkingCheck | null;
  /** Via stops on the way, in order (mirrors `route.via`); empty or missing = none. */
  stops?: Place[];
};

/** POST /api/nudges body (truck_profile is added by the client). */
export type NudgeRequest = {
  lat: number;
  lng: number;
  driving: boolean;
  active_place?: Place | null;
  /** Route points ahead as [lat, lng, miles_ahead], nearest first (see geo/ahead.ts). */
  ahead: [number, number, number][];
  drive_elapsed_min?: number | null;
  local_hour?: number | null;
  session_id?: string | null;
  /** Left out when no active control is ahead or its position is unknown (see geo/ahead.ts). */
  chain_control_ahead?: ChainControlAhead;
};

export type Nudge = {
  kind: "break" | "parking" | "chains";
  text: string;
  priority: number;
  place?: Place | null;
  /** What a "yes" on the next turn does; the backend keeps it pending for the session. */
  offer?: "add_stop" | "go" | "chains_answer" | null;
};

export type Phase = "ready" | "listening" | "thinking" | "options" | "navigating" | "providers";

export type ParkingState = { loading: boolean; result?: ParkingCheck };
