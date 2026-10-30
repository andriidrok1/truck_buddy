// The camera that shows a set of points: a centre and a fractional zoom for a view of w x h
// layout units. Pure Web Mercator maths, so the native map gets one animateCamera() instead of
// fitToCoordinates(): on Android the latter mixes its edge padding with the map padding and the
// screen density, and a 5-mile route came out at the zoom of half the state (QA 2026-10-28).
import type { LatLng } from "../types";

const TILE = 256;

/** World pixel of a point at zoom `z` (tile size 256). */
export function project(p: LatLng, z: number): { x: number; y: number } {
  const s = TILE * 2 ** z;
  const lat = Math.max(-85.05, Math.min(85.05, p.lat));
  const sin = Math.sin((lat * Math.PI) / 180);
  return { x: ((p.lng + 180) / 360) * s, y: (0.5 - Math.log((1 + sin) / (1 - sin)) / (4 * Math.PI)) * s };
}

export function unproject(x: number, y: number, z: number): LatLng {
  const s = TILE * 2 ** z;
  const n = Math.PI - (2 * Math.PI * y) / s;
  return { lat: (180 / Math.PI) * Math.atan(0.5 * (Math.exp(n) - Math.exp(-n))), lng: (x / s) * 360 - 180 };
}

export type Camera = { center: LatLng; zoom: number };

/**
 * Centre and zoom that keep every point at least `margin` units from the edge of a `w` x `h` view.
 * Zoom is fractional and clamped to [minZoom, maxZoom]; one point gets `maxZoom`.
 */
export function fitCamera(points: readonly LatLng[], w: number, h: number, margin = 56, maxZoom = 16, minZoom = 3): Camera {
  if (points.length === 0) return { center: { lat: 0, lng: 0 }, zoom: minZoom };
  const px = points.map((p) => project(p, 0));
  const xs = px.map((p) => p.x);
  const ys = px.map((p) => p.y);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  const center = unproject((minX + maxX) / 2, (minY + maxY) / 2, 0);
  const availW = Math.max(40, w - 2 * margin);
  const availH = Math.max(40, h - 2 * margin);
  const zw = maxX > minX ? Math.log2(availW / (maxX - minX)) : maxZoom;
  const zh = maxY > minY ? Math.log2(availH / (maxY - minY)) : maxZoom;
  const zoom = Math.max(minZoom, Math.min(maxZoom, zw, zh));
  return { center, zoom: Math.round(zoom * 100) / 100 };
}
