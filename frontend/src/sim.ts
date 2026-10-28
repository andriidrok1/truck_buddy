import { useEffect, useState } from "react";

import type { TracePoint } from "./geo/trace";
import type { LatLng } from "./types";

/** Tester-only fake GPS: the app behaves as if the truck were at this spot. */
export type SimLocation = {
  id: string;
  label: string;
  coords: LatLng;
  heading: number;
  /** m/s; omitted = standing still (null). */
  speed?: number | null;
  /** metres; omitted = a tight 5 m fix. */
  accuracy?: number | null;
  /** epoch ms; setSim() stamps the moment the spot was chosen. */
  ts?: number;
  /** Set while playTrace() drives this spot along a recorded/synthetic trace (the truck "moves"). */
  trace?: TracePoint[];
};

/** Made-up test spots. */
export const SCENARIOS: (SimLocation & { title: string; hint: string; icon: string })[] = [
  {
    id: "flat-tire-i80",
    title: "Flat tire on I-80",
    label: "Test: flat tire, I-80 E near Vacaville",
    hint: 'Then say: "I\'ve got a flat tire"',
    icon: "car-tire-alert",
    coords: { lat: 38.38585, lng: -121.93664 },
    heading: 52,
  },
];

let sim: SimLocation | null = null;
const listeners = new Set<() => void>();

export const getSim = () => sim;

export function setSim(s: SimLocation | null) {
  if (s === null || !s.trace) stopTrace(); // a plain spot (or real GPS) ends any replay
  sim = s ? { ts: Date.now(), ...s } : null;
  listeners.forEach((l) => l());
}

export function useSim(): SimLocation | null {
  const [, force] = useState(0);
  useEffect(() => {
    const l = () => force((n) => n + 1);
    listeners.add(l);
    return () => {
      listeners.delete(l);
    };
  }, []);
  return sim;
}

// ---- Trace replay (tester screen): feeds the trace points through setSim() on timers. ----

let traceTimer: ReturnType<typeof setTimeout> | null = null;
let tracePlaying = false;

export const isTracePlaying = () => tracePlaying;

/**
 * Plays `trace` as the simulated truck, `speedFactor` times faster than real time. The fix
 * timestamps keep the trace's own clock (start + t_ms), not the wall clock, so the off-route
 * tracker sees the same gaps and implied speeds at 4x as at 1x. The sim stays on the last point
 * when the trace ends; stopTrace() or setSim() interrupts it.
 */
export function playTrace(trace: TracePoint[], speedFactor = 1, spot: Pick<SimLocation, "id" | "label"> = { id: "trace", label: "Replay" }) {
  stopTrace();
  if (!trace.length) return;
  const factor = speedFactor > 0 ? speedFactor : 1;
  const startWall = Date.now();
  const t0 = trace[0].t_ms;
  tracePlaying = true;
  const apply = (p: TracePoint) =>
    setSim({
      ...spot,
      trace,
      coords: { lat: p.lat, lng: p.lng },
      heading: p.heading,
      speed: p.speed,
      accuracy: p.accuracy,
      ts: startWall + (p.t_ms - t0),
    });
  const step = (i: number) => {
    apply(trace[i]);
    if (i + 1 >= trace.length) {
      traceTimer = null;
      tracePlaying = false;
      listeners.forEach((l) => l());
      return;
    }
    const due = startWall + (trace[i + 1].t_ms - t0) / factor;
    traceTimer = setTimeout(() => step(i + 1), Math.max(0, due - Date.now()));
  };
  step(0);
}

export function stopTrace() {
  if (traceTimer) clearTimeout(traceTimer);
  traceTimer = null;
  if (tracePlaying) {
    tracePlaying = false;
    listeners.forEach((l) => l());
  }
}
