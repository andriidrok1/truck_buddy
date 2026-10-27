// Synthetic GPS traces along a route polyline, for the tester's replay buttons and the off-route
// tests. Pure: the same geometry maths as guidance.ts, no React.

import { cumulativeMeters } from "./guidance";

export type TracePoint = {
  lat: number;
  lng: number;
  /** Direction of travel, degrees clockwise from north. */
  heading: number;
  /** m/s; 0 while stopped. */
  speed: number;
  /** Horizontal accuracy, metres. */
  accuracy: number;
  /** Milliseconds since the start of the trace. */
  t_ms: number;
};

export type SyntheticTraceOptions = {
  /** Fix index at which the truck leaves the line (perpendicular, to the right of travel). */
  detourAtIndex?: number;
  /** How far off the line the detour goes, metres. */
  detourM?: number;
  /** How many fixes the detour lasts; the truck is back on the line afterwards. */
  detourPoints?: number;
  /** Fix index at which the truck stops (speed 0, same spot) for `stopS` seconds. */
  stopAtIndex?: number;
  stopS?: number;
  /** Seconds between fixes. */
  stepS?: number;
  /** Accuracy radius written on every fix. */
  accuracyM?: number;
};

const RAD = Math.PI / 180;
const DEG_PER_M_LAT = 1 / 111195;

/** Initial bearing from a to b, degrees 0..359. */
export function bearingDeg(a: readonly [number, number], b: readonly [number, number]): number {
  const dLng = (b[1] - a[1]) * RAD;
  const y = Math.sin(dLng) * Math.cos(b[0] * RAD);
  const x = Math.cos(a[0] * RAD) * Math.sin(b[0] * RAD) - Math.sin(a[0] * RAD) * Math.cos(b[0] * RAD) * Math.cos(dLng);
  return ((Math.atan2(y, x) / RAD) + 360) % 360;
}

/** Point `m` metres from `p` in direction `bearing` (small-distance approximation, fine for metres). */
export function offsetM(p: readonly [number, number], bearing: number, m: number): [number, number] {
  const dLat = m * Math.cos(bearing * RAD) * DEG_PER_M_LAT;
  const dLng = (m * Math.sin(bearing * RAD) * DEG_PER_M_LAT) / Math.cos(p[0] * RAD);
  return [p[0] + dLat, p[1] + dLng];
}

/**
 * Walks `geometry` at `mps`, one fix every `stepS` seconds, from the first vertex to the last
 * (the last step is cut short to end exactly there).
 * Headings follow the segment being walked. The detour shifts whole fixes sideways by `detourM`
 * (a step, not a ramp: the tracker's jump filter sees one skip, then counts), the stop repeats the
 * same position with speed 0 for `stopS` seconds and then carries on.
 */
export function syntheticTrace(geometry: readonly (readonly [number, number])[], mps: number, opts: SyntheticTraceOptions = {}): TracePoint[] {
  const { detourAtIndex, detourM = 0, detourPoints = 0, stopAtIndex, stopS = 0, stepS = 1, accuracyM = 5 } = opts;
  if (geometry.length === 0 || mps <= 0 || stepS <= 0) return [];
  const cum = cumulativeMeters(geometry);
  const total = cum[cum.length - 1];
  const stepM = mps * stepS;
  const out: TracePoint[] = [];
  let seg = 0;
  let t = 0;
  // The last fix sits on the final vertex even when the route is not a whole number of steps
  // (and when float rounding puts a 1000 m line at 999.999 m).
  const steps = Math.max(0, Math.ceil(total / stepM - 1e-6));
  for (let k = 0; k <= steps; k++) {
    const d = Math.min(total, k * stepM);
    while (seg < geometry.length - 2 && cum[seg + 1] < d) seg++;
    const a = geometry[seg];
    const b = geometry[Math.min(seg + 1, geometry.length - 1)];
    const len = cum[seg + 1] - cum[seg];
    const f = len > 0 ? Math.min(1, Math.max(0, (d - cum[seg]) / len)) : 0;
    const heading = geometry.length > 1 ? bearingDeg(a, b) : 0;
    let p: [number, number] = [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f];
    const i = k; // detour/stop indices count walk steps: the stopped fixes do not shift them
    if (detourAtIndex != null && detourM > 0 && i >= detourAtIndex && i < detourAtIndex + detourPoints) {
      p = offsetM(p, (heading + 90) % 360, detourM);
    }
    if (stopAtIndex != null && stopS > 0 && i === stopAtIndex) {
      const n = Math.round(stopS / stepS);
      for (let s = 0; s < n; s++) {
        out.push({ lat: p[0], lng: p[1], heading, speed: 0, accuracy: accuracyM, t_ms: t });
        t += stepS * 1000;
      }
    }
    out.push({ lat: p[0], lng: p[1], heading, speed: mps, accuracy: accuracyM, t_ms: t });
    t += stepS * 1000;
  }
  return out;
}
