// Route points ahead of the truck for POST /api/nudges: one vertex every ~`stepMi` miles up to
// `maxMi`, nearest first, as [lat, lng, miles_ahead], plus the first active chain control ahead.
// Pure: works off the geometry, its cumulative metres (guidance.cumulativeMeters) and the current
// route match.

import type { ChainControl, ChainControlAhead } from "../types";

const M_PER_MI = 1609.34;
/** NudgeRequest.chain_control_ahead.miles_ahead is 0..500. */
export const CHAIN_AHEAD_MAX_MI = 500;
/** The backend rejects more than this many points. */
export const AHEAD_MAX_POINTS = 40;

export type AheadPoint = [lat: number, lng: number, milesAhead: number];

/**
 * For k = 1, 2, ... the first vertex at least k * stepMi miles past the projected position
 * (alongM), stopping at maxMi or the end of the route. Every point is strictly after the matched
 * segment; nothing when there is no match yet or the route ends within the first step.
 */
export function routePointsAhead(
  geometry: readonly (readonly [number, number])[],
  cum: readonly number[],
  matchedIndex: number,
  alongM: number,
  stepMi = 5,
  maxMi = 60,
): AheadPoint[] {
  const out: AheadPoint[] = [];
  if (matchedIndex < 0 || geometry.length === 0 || cum.length !== geometry.length || stepMi <= 0) return out;
  let i = matchedIndex + 1;
  for (let k = 1; k * stepMi <= maxMi && out.length < AHEAD_MAX_POINTS; k++) {
    // A vertex a metre short of the mark still counts (summed legs land just under round distances).
    const target = alongM + k * stepMi * M_PER_MI - 1;
    while (i < geometry.length && cum[i] < target) i++;
    if (i >= geometry.length) break;
    const [lat, lng] = geometry[i];
    out.push([lat, lng, Math.round(((cum[i] - alongM) / M_PER_MI) * 10) / 10]);
    i++;
  }
  return out;
}

/** "R-2", "R2", "r-2 chains required" -> "R-2"; anything else (lifted, "None") is not active. */
export function chainStatus(status: string): ChainControlAhead["status"] | null {
  const m = /(?:^|[^A-Z])R-?([123])/.exec(status.toUpperCase().replace(/\s+/g, ""));
  return m ? (`R-${m[1]}` as ChainControlAhead["status"]) : null;
}

/**
 * The nearest active (R-1..R-3) chain control past the matched segment, with the route miles
 * from the projected position (alongM) to it. Null without a match, when none is ahead, when a
 * control's geometry index is outside the route, or when the nearest is past the 500 mi the
 * backend accepts.
 */
export function chainControlAhead(
  controls: readonly ChainControl[] | undefined,
  cum: readonly number[],
  matchedIndex: number,
  alongM: number,
): ChainControlAhead | null {
  if (!controls?.length || matchedIndex < 0 || !cum.length) return null;
  let best: ChainControlAhead | null = null;
  for (const c of controls) {
    const status = chainStatus(c.status);
    if (!status || !Number.isInteger(c.index) || c.index <= matchedIndex || c.index >= cum.length) continue;
    const mi = Math.round(((cum[c.index] - alongM) / M_PER_MI) * 10) / 10;
    if (mi < 0 || mi > CHAIN_AHEAD_MAX_MI) continue;
    if (!best || mi < best.miles_ahead) best = { route: c.route, location: c.location, status, miles_ahead: mi };
  }
  return best;
}
