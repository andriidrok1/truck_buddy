import type { ParkingCheck, ParkingSpot, RouteInfo } from "../types";

/** Truck entrance (E) from the satellite check, if one was marked. */
export function entrySpot(pk?: ParkingCheck | null): ParkingSpot | null {
  return pk?.spots?.find((s) => s.kind === "entry") ?? null;
}

const meters = (a: [number, number], b: [number, number]) =>
  Math.hypot((a[0] - b[0]) * 111320, (a[1] - b[1]) * 111320 * Math.cos((a[0] * Math.PI) / 180));

/** The last `max` meters of a route line (whole segments). */
export function routeTail(geometry: [number, number][], max = 300): [number, number][] {
  const out: [number, number][] = [];
  let d = 0;
  for (let i = geometry.length - 1; i >= 0; i--) {
    out.unshift(geometry[i]);
    if (i === 0) break;
    d += meters(geometry[i], geometry[i - 1]);
    if (d >= max) {
      out.unshift(geometry[i - 1]);
      break;
    }
  }
  return out;
}

/** True when the route already ends at the entrance (within ~40 m). */
export function routeEndsAt(route: RouteInfo, s: ParkingSpot): boolean {
  const end = route.geometry[route.geometry.length - 1];
  if (!end) return false;
  const dLat = (end[0] - s.lat) * 111320;
  const dLng = (end[1] - s.lng) * 111320 * Math.cos((s.lat * Math.PI) / 180);
  return Math.hypot(dLat, dLng) < 40;
}
