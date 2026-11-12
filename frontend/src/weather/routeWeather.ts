// Weather along the route at the time the truck gets there (Figma "Weather v2": chips on the map, the line in
// the route card, "Weather on the way"). Pure: sampling, which chips show at which zoom, what the card says.
// The numbers come from POST /api/weather/points (backend services/route_weather.py, NWS forecast grid).

import { t } from "../i18n";
import type { RouteInfo, WeatherAlert } from "../types";
import type { components } from "../types.generated";

export type RoutePoint = components["schemas"]["RoutePoint"];
export type PointSample = { index: number; lat: number; lng: number; eta: string };

export const SAMPLE_KM = 40;
export const MAX_SAMPLES = 14;
/** Below this map zoom only what matters stays on the map; the rest folds into dots (Figma V2a / V2b). */
export const DETAIL_ZOOM = 8;

const R_KM = 6371;
function km(a: [number, number], b: [number, number]): number {
  const toRad = (d: number) => (d * Math.PI) / 180;
  const dLat = toRad(b[0] - a[0]);
  const dLng = toRad(b[1] - a[1]);
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(toRad(a[0])) * Math.cos(toRad(b[0])) * Math.sin(dLng / 2) ** 2;
  return 2 * R_KM * Math.asin(Math.sqrt(h));
}

/** The route vertex closest to a point (the truck), for counting what is still ahead. */
export function nearestIndex(geometry: [number, number][], p: { lat: number; lng: number }): number {
  let best = 0;
  let bestD = Infinity;
  for (let i = 0; i < geometry.length; i++) {
    const dLat = geometry[i][0] - p.lat;
    const dLng = (geometry[i][1] - p.lng) * Math.cos((p.lat * Math.PI) / 180);
    const d = dLat * dLat + dLng * dLng;
    if (d < bestD) {
      bestD = d;
      best = i;
    }
  }
  return best;
}

/** Points about every `everyKm` along the route from `fromIndex` (where the truck is) to the end, each with its
 *  ETA from now by the share of the route's length still to cover: the backend's weather.eta_at rule, counted
 *  from the truck, so a refresh mid-drive or after an app restart asks for the part ahead at its real times.
 *  Long routes get a wider step to stay within `max`. */
export function samplePoints(geometry: [number, number][], durationS: number, now: Date, everyKm = SAMPLE_KM, max = MAX_SAMPLES, fromIndex = 0): PointSample[] {
  if (geometry.length === 0) return [];
  const from = Math.max(0, Math.min(fromIndex, geometry.length - 1));
  const steps = geometry.slice(1).map((p, i) => km(geometry[i], p));
  const total = steps.reduce((a, b) => a + b, 0);
  const ahead = steps.slice(from).reduce((a, b) => a + b, 0);
  const step = Math.max(everyKm, ahead / Math.max(1, max - 1));
  const at = (before: number) => new Date(now.getTime() + (total > 0 ? (before / total) * durationS * 1000 : 0)).toISOString();
  const out: PointSample[] = [{ index: from, lat: geometry[from][0], lng: geometry[from][1], eta: at(0) }];
  let before = 0;
  let since = 0;
  for (let i = from + 1; i < geometry.length; i++) {
    before += steps[i - 1];
    since += steps[i - 1];
    if (since >= step && out.length < max - 1 && i < geometry.length - 1) {
      out.push({ index: i, lat: geometry[i][0], lng: geometry[i][1], eta: at(before) });
      since = 0;
    }
  }
  const last = geometry.length - 1;
  if (last > from) out.push({ index: last, lat: geometry[last][0], lng: geometry[last][1], eta: at(ahead) });
  return out;
}

/** The NWS alert on the route that covers this point, if any (alerts carry the geometry index span). */
export function alertAt(p: { index: number }, alerts: readonly WeatherAlert[] | undefined, lastIndex: number): WeatherAlert | null {
  for (const a of alerts ?? []) {
    const end = a.index_end ?? lastIndex;
    if (p.index >= a.index && p.index <= end) return a;
  }
  return null;
}

export type Chip = RoutePoint & { warn: boolean; alert: WeatherAlert | null };

/** Every point with its final weight: warn when the server says so (ice, gusts, low visibility) or an alert
 *  covers it. */
export function chips(points: readonly RoutePoint[], route: Pick<RouteInfo, "weather" | "geometry">): Chip[] {
  const last = route.geometry.length - 1;
  return points.map((p) => {
    const alert = alertAt(p, route.weather, last);
    return { ...p, alert, warn: p.severity === "warn" || !!alert };
  });
}

/** What the map draws at a zoom: the warn chips always, the others as chips only when zoomed in (else a dot). */
export function chipMode(c: Chip, zoom: number): "chip" | "dot" {
  return c.warn || zoom >= DETAIL_ZOOM ? "chip" : "dot";
}

/** Web-mercator zoom from a react-native-maps region's longitudeDelta and the map's width in points. */
export function zoomOf(longitudeDelta: number, widthPt: number): number {
  return Math.log2((360 * (widthPt / 256)) / Math.max(1e-6, longitudeDelta));
}

/** The chip's main number: gusts for a wind chip, else the temperature. */
export function chipValue(c: RoutePoint): string {
  if (c.kind === "wind" && c.gust_mph != null) return `${c.gust_mph}`;
  return c.temp_f != null ? `${c.temp_f}°` : "";
}

export const KIND_ICON: Record<string, string> = {
  ice: "snowflake-alert",
  snow: "weather-snowy-heavy",
  wind: "weather-windy",
  fog: "weather-fog",
  rain: "weather-pouring",
  cloud: "weather-cloudy",
  clear: "weather-sunny",
};

/** One line for the route card: the first point that matters, in English (the Text component translates). */
export function cardLine(list: readonly Chip[], clock: (iso: string) => string): { kind: string; title: string; sub: string } | null {
  const c = list.find((x) => x.warn);
  if (!c) return null;
  const where = c.place?.split(",")[0] ?? "the route";
  const when = clock(c.eta);
  // Built through t() with the values (an exact key, never a guessed pattern): "Snow near X" must not be read
  // as "{event} near X".
  const v = { where, when, gust: String(c.gust_mph ?? ""), event: c.alert?.event ?? "" };
  const title =
    c.kind === "ice" ? t("Ice risk near {where} around {when}", v) :
    c.kind === "snow" ? t("Snow near {where} around {when}", v) :
    c.kind === "wind" ? t("Gusts {gust} mph near {where} around {when}", v) :
    c.kind === "fog" ? t("Fog near {where} around {when}", v) :
    c.alert ? t("{event} near {where} around {when}", v) : t("Weather near {where} around {when}", v);
  return { kind: c.kind, title, sub: c.alert ? c.alert.event : t("NWS forecast for when you get there") };
}

/** "Rain, fog patches ½ mi" style detail for a row of "Weather on the way". */
export function detail(c: Chip): string {
  const parts: string[] = [];
  const name = { ice: "Ice risk", snow: "Snow", wind: "Strong gusts", fog: "Fog", rain: "Rain", cloud: "Cloudy", clear: "Clear sky" }[c.kind] ?? c.kind;
  parts.push(t(name));
  if (c.gust_mph != null && c.gust_mph >= 30 && c.kind !== "wind") parts.push(t("gusts {gust} mph", { gust: String(c.gust_mph) }));
  if (c.vis_mi != null && c.vis_mi < 3) parts.push(t("visibility {vis} mi", { vis: String(c.vis_mi) }));
  if (c.alert) parts.push(c.alert.event);
  return parts.join(" · ");
}

export type WeatherAhead = {
  key: string;
  kind: string;
  place?: string | null;
  minutes_ahead: number;
  temp_f?: number | null;
  gust_mph?: number | null;
  vis_mi?: number | null;
  event?: string | null;
};

/** The first point ahead of the truck that matters, for the copilot's heads-up (POST /api/nudges). Minutes by
 *  the distance still to go at the route's average speed: ETAs from when the route was set drift with traffic. */
export function weatherAhead(list: readonly Chip[], route: Pick<RouteInfo, "distance_m" | "duration_s">, cum: readonly number[], matchedIndex: number, alongM: number): WeatherAhead | null {
  if (matchedIndex < 0 || !cum.length || route.duration_s <= 0) return null;
  const mps = route.distance_m / route.duration_s;
  const c = list.find((x) => x.warn && x.index > matchedIndex && x.index < cum.length);
  if (!c || mps <= 0) return null;
  const minutes = Math.max(0, Math.round((cum[c.index] - alongM) / mps / 60));
  return {
    key: `${c.kind}-${c.index}`,
    kind: c.kind,
    place: c.place ?? null,
    minutes_ahead: Math.min(24 * 60, minutes),
    temp_f: c.temp_f ?? null,
    gust_mph: c.gust_mph ?? null,
    vis_mi: c.vis_mi ?? null,
    event: c.alert?.event ?? null,
  };
}
