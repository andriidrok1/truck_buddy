import { useEffect, useState } from "react";

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
