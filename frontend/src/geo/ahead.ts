// Route points ahead of the truck for POST /api/nudges: one vertex every ~`stepMi` miles up to
// `maxMi`, nearest first, as [lat, lng, miles_ahead]. Pure: works off the geometry, its
// cumulative metres (guidance.cumulativeMeters) and the current route match.

const M_PER_MI = 1609.34;
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
