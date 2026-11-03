import { haversineM } from "./guidance";
import type { LatLng, Place } from "../types";

/** Within this of a stop or the destination and standing (or slower than a walk), the truck is there. */
export const ARRIVE_M = 150;
export const ARRIVE_SPEED_MPS = 3;

export type Arrival = { place: Place; kind: "stop" | "destination" };

/**
 * Places of this trip the truck has just reached: each one once per trip (`seen` holds the ids already
 * reported). A trip event per arrival is what the server counts visits by and the fleet reads arrival times
 * from; nothing reported it before (2026-11-02). Pure: the screen or the trip engine feeds it every fix.
 */
export function arrivals(
  fix: LatLng & { speed: number | null },
  stops: readonly Place[] | undefined,
  destination: Place | null | undefined,
  seen: Set<string>,
): Arrival[] {
  if (fix.speed != null && fix.speed > ARRIVE_SPEED_MPS) return [];
  const out: Arrival[] = [];
  const at = (p: Place) => !seen.has(p.id) && haversineM(fix, { lat: p.lat, lng: p.lng }) <= ARRIVE_M;
  for (const s of stops ?? []) {
    if (at(s)) {
      seen.add(s.id);
      out.push({ place: s, kind: "stop" });
    }
  }
  if (destination && at(destination)) {
    seen.add(destination.id);
    out.push({ place: destination, kind: "destination" });
  }
  return out;
}
