import type { ParkingCheck, ParkingSpot, RouteInfo } from "../types";

/** Truck entrance (E) from the satellite check, if one was marked. */
export function entrySpot(pk?: ParkingCheck | null): ParkingSpot | null {
  return pk?.spots?.find((s) => s.kind === "entry") ?? null;
}

/** True when the route already ends at the entrance (within ~40 m). */
export function routeEndsAt(route: RouteInfo, s: ParkingSpot): boolean {
  const end = route.geometry[route.geometry.length - 1];
  if (!end) return false;
  const dLat = (end[0] - s.lat) * 111320;
  const dLng = (end[1] - s.lng) * 111320 * Math.cos((s.lat * Math.PI) / 180);
  return Math.hypot(dLat, dLng) < 40;
}
