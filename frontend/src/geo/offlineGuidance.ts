// What the copilot does about the route when the signal is gone. Pure: no React, no timers,
// no network; the hook (useOfflineGuidance) feeds it the clock and speaks what it hands back.

import { formatDistance, haversineM, projectOnRoute } from "./guidance";
import type { LatLng } from "../types";

export const NET_LOST_LINE = "Lost signal. I'll keep you on the loaded route.";
export const NET_BACK_LINE = "Signal's back.";
export const NO_SIGNAL_TURN_LINE = "No signal right now. I can still read your turns and what we loaded.";
/** A state must hold this long before it is announced (a flapping link is not news). */
export const NET_HOLD_MS = 5000;
/** "The route is 0.4 miles to the northeast" at most this often. */
export const BACK_ON_ROUTE_GAP_MS = 60000;

export type NetAnnouncement = "lost" | "back";

export type NetAnnouncer = {
  /**
   * Current state at `now`. Call on every change and again `holdMs` later: the answer is "lost"
   * the first time the offline state has held for `holdMs`, "back" once online has held that long
   * after a "lost", null otherwise. Unknown (null) is never announced.
   */
  tick(online: boolean | null, now: number): NetAnnouncement | null;
  reset(): void;
};

export function createNetAnnouncer(holdMs = NET_HOLD_MS): NetAnnouncer {
  let candidate: boolean | null | undefined = undefined;
  let since = 0;
  let announced = true; // the app starts "online": the first thing worth saying is a loss
  return {
    tick(online, now) {
      if (online !== candidate) {
        candidate = online;
        since = now;
      }
      if (candidate == null) return null;
      if (now - since < holdMs) return null;
      if (candidate === announced) return null;
      announced = candidate;
      return candidate ? "back" : "lost";
    },
    reset() {
      candidate = undefined;
      since = 0;
      announced = true;
    },
  };
}

export const COMPASS = ["north", "northeast", "east", "southeast", "south", "southwest", "west", "northwest"] as const;
export type Compass = (typeof COMPASS)[number];
export const COMPASS_SHORT: Record<Compass, string> = {
  north: "N", northeast: "NE", east: "E", southeast: "SE", south: "S", southwest: "SW", west: "W", northwest: "NW",
};

/** Initial bearing from `a` to `b`, degrees 0..360 clockwise from north. */
export function bearingDeg(a: LatLng, b: LatLng): number {
  const rad = Math.PI / 180;
  const dLng = (b.lng - a.lng) * rad;
  const y = Math.sin(dLng) * Math.cos(b.lat * rad);
  const x = Math.cos(a.lat * rad) * Math.sin(b.lat * rad) - Math.sin(a.lat * rad) * Math.cos(b.lat * rad) * Math.cos(dLng);
  return ((Math.atan2(y, x) * 180) / Math.PI + 360) % 360;
}

export const compassOf = (deg: number): Compass => COMPASS[Math.round((((deg % 360) + 360) % 360) / 45) % 8];

/** "0.4 miles", "1 mile", "300 feet" (spoken). */
export function spokenMiles(m: number): string {
  const mi = m / 1609.34;
  if (mi < 0.1) return `${Math.max(50, Math.round((m * 3.28084) / 50) * 50)} feet`;
  const s = mi < 10 ? mi.toFixed(1) : String(Math.round(mi));
  return s === "1.0" || s === "1" ? "1 mile" : `${s} miles`;
}

export type BackOnRoute = {
  /** Straight-line metres from the truck to the nearest point of the route. */
  distM: number;
  bearingDeg: number;
  compass: Compass;
  /** Where that point is relative to the truck's heading, when a heading is known. */
  relative: "ahead" | "behind" | "to your left" | "to your right" | null;
  /** The spoken line. */
  text: string;
  /** The banner line: "Return to the route: 0.4 mi NE". */
  line: string;
};

/**
 * No reroute without the network: point the driver back at the loaded line instead. Nearest
 * point of the route near `matchedIndex` (the guidance match), its distance and compass
 * direction from the truck. `heading` only adds the relative word; the text keeps the compass
 * (a stopped truck has no heading, and "northeast" reads the same on the banner).
 */
export function backOnRoute(
  geometry: readonly (readonly [number, number])[],
  cum: readonly number[],
  matchedIndex: number,
  driver: LatLng,
  heading: number | null = null,
): BackOnRoute | null {
  if (geometry.length === 0) return null;
  const m = projectOnRoute(geometry, cum, driver, matchedIndex >= 0 ? matchedIndex : null);
  const a = geometry[m.index];
  const b = geometry[Math.min(m.index + 1, geometry.length - 1)];
  const point: LatLng = { lat: a[0] + (b[0] - a[0]) * m.t, lng: a[1] + (b[1] - a[1]) * m.t };
  const distM = haversineM(driver, point);
  const bearing = bearingDeg(driver, point);
  const compass = compassOf(bearing);
  let relative: BackOnRoute["relative"] = null;
  if (heading != null) {
    const rel = (((bearing - heading) % 360) + 360) % 360;
    relative = rel < 45 || rel >= 315 ? "ahead" : rel < 135 ? "to your right" : rel < 225 ? "behind" : "to your left";
  }
  return {
    distM,
    bearingDeg: bearing,
    compass,
    relative,
    text: `Lost signal, can't reroute. The route is ${spokenMiles(distM)} to the ${compass}.`,
    line: `Return to the route: ${formatDistance(distM)} ${COMPASS_SHORT[compass]}`,
  };
}
