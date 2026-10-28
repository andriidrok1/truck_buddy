// Bounding boxes, pack names and size estimates for offline corridor packs. Pure: no MapLibre,
// no React. MapLibre's offline regions are rectangles, so a corridor pack is the route's bbox
// plus a buffer; the estimate counts Web Mercator tiles z6..z14 over it at ~25 KB per vector tile.

import type { LatLng, RouteInfo } from "../types";

export type Bounds = { sw: LatLng; ne: LatLng };

/** MapLibre's [west, south, east, north]. */
export type LngLatBounds = [number, number, number, number];

export const PACK_MIN_ZOOM = 6;
export const PACK_MAX_ZOOM = 14;
/** Rough average OpenFreeMap vector tile at the zooms a corridor pack holds. */
export const KB_PER_TILE = 25;
/** Buffer around the route's bbox, so an exit or a parking lot next to it is on the map too. */
export const CORRIDOR_BUFFER_KM = 3;

const KM_PER_DEG_LAT = 111.32;

/** Bbox of the polyline, padded by `bufferKm` on every side (clamped to the Mercator range). null for no points. */
export function routeBounds(geometry: readonly (readonly [number, number])[], bufferKm = CORRIDOR_BUFFER_KM): Bounds | null {
  if (!geometry.length) return null;
  let s = Infinity;
  let n = -Infinity;
  let w = Infinity;
  let e = -Infinity;
  for (const [lat, lng] of geometry) {
    if (lat < s) s = lat;
    if (lat > n) n = lat;
    if (lng < w) w = lng;
    if (lng > e) e = lng;
  }
  const dLat = bufferKm / KM_PER_DEG_LAT;
  const midLat = (s + n) / 2;
  const dLng = bufferKm / (KM_PER_DEG_LAT * Math.max(0.2, Math.cos((midLat * Math.PI) / 180)));
  return {
    sw: { lat: Math.max(-85, s - dLat), lng: Math.max(-180, w - dLng) },
    ne: { lat: Math.min(85, n + dLat), lng: Math.min(180, e + dLng) },
  };
}

export const toLngLatBounds = (b: Bounds): LngLatBounds => [b.sw.lng, b.sw.lat, b.ne.lng, b.ne.lat];

/**
 * Stable 32-bit FNV-1a hash of the geometry (coordinates at 5 decimals, ~1 m), hex. The same
 * route always gets the same pack name; a reroute with a new line gets a new one.
 */
export function geometryHash(geometry: readonly (readonly [number, number])[]): string {
  let h = 0x811c9dc5;
  const mix = (s: string) => {
    for (let i = 0; i < s.length; i++) {
      h ^= s.charCodeAt(i);
      h = Math.imul(h, 0x01000193) >>> 0;
    }
  };
  for (const [lat, lng] of geometry) mix(`${lat.toFixed(5)},${lng.toFixed(5)};`);
  return h.toString(16).padStart(8, "0");
}

export type PackSpec = { name: string; bounds: Bounds; minZoom: number; maxZoom: number };

/** The pack that covers this route: "route-<hash>", its buffered bbox, z6..z14. null without geometry. */
export function corridorPack(route: Pick<RouteInfo, "geometry">, bufferKm = CORRIDOR_BUFFER_KM): PackSpec | null {
  const bounds = routeBounds(route.geometry, bufferKm);
  if (!bounds) return null;
  return { name: `route-${geometryHash(route.geometry)}`, bounds, minZoom: PACK_MIN_ZOOM, maxZoom: PACK_MAX_ZOOM };
}

const lngToX = (lng: number, z: number) => Math.floor(((lng + 180) / 360) * 2 ** z);
const latToY = (lat: number, z: number) => {
  const r = (lat * Math.PI) / 180;
  return Math.floor(((1 - Math.log(Math.tan(r) + 1 / Math.cos(r)) / Math.PI) / 2) * 2 ** z);
};

/** Web Mercator tiles that cover the bbox at one zoom. */
export function tilesAtZoom(b: Bounds, z: number): number {
  const max = 2 ** z - 1;
  const x0 = Math.max(0, Math.min(max, lngToX(b.sw.lng, z)));
  const x1 = Math.max(0, Math.min(max, lngToX(b.ne.lng, z)));
  const y0 = Math.max(0, Math.min(max, latToY(b.ne.lat, z)));
  const y1 = Math.max(0, Math.min(max, latToY(b.sw.lat, z)));
  return (x1 - x0 + 1) * (y1 - y0 + 1);
}

export function tileCount(b: Bounds, minZoom = PACK_MIN_ZOOM, maxZoom = PACK_MAX_ZOOM): number {
  let n = 0;
  for (let z = minZoom; z <= maxZoom; z++) n += tilesAtZoom(b, z);
  return n;
}

/** Bytes a pack over `b` will take, at KB_PER_TILE per tile. */
export function estimateBytes(b: Bounds, minZoom = PACK_MIN_ZOOM, maxZoom = PACK_MAX_ZOOM): number {
  return tileCount(b, minZoom, maxZoom) * KB_PER_TILE * 1024;
}

/** "84 MB", "900 KB", "1.2 GB". */
export function formatBytes(n: number): string {
  if (n < 1024 * 1024) return `${Math.max(1, Math.round(n / 1024))} KB`;
  const mb = n / (1024 * 1024);
  if (mb < 1000) return `${mb < 10 ? mb.toFixed(1) : Math.round(mb)} MB`;
  return `${(mb / 1024).toFixed(1)} GB`;
}
