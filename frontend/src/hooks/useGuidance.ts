import { useEffect, useMemo, useRef } from "react";

import type { ActiveRoute, LatLng, Maneuver } from "../types";

const R = 6371000;
function meters(a: LatLng, b: LatLng) {
  const dLat = ((b.lat - a.lat) * Math.PI) / 180;
  const dLng = ((b.lng - a.lng) * Math.PI) / 180;
  const s = Math.sin(dLat / 2) ** 2 + Math.cos((a.lat * Math.PI) / 180) * Math.cos((b.lat * Math.PI) / 180) * Math.sin(dLng / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(s));
}

/** "0.4 mi" / "500 ft" for the banner. */
export function formatDistance(m: number) {
  const ft = m * 3.28084;
  if (ft < 1000) return `${Math.max(50, Math.round(ft / 50) * 50)} ft`;
  const mi = m / 1609.34;
  return mi < 10 ? `${mi.toFixed(1)} mi` : `${Math.round(mi)} mi`;
}

/** Spoken lead-in before the maneuver: "In half a mile", "In 500 feet". */
function spokenDistance(m: number) {
  if (m > 1200) return "In about a mile";
  if (m > 600) return "In half a mile";
  return "In 500 feet";
}

// Announce once ~1 mi out, once ~0.5 mi / 500 ft out, then right at the turn.
const STAGES = [
  { key: "far", max: 1800, min: 1000 },
  { key: "near", max: 900, min: 120 },
  { key: "now", max: 90, min: 0 },
];

export type Guidance = { next: Maneuver | null; toNextM: number; remainingM: number; remainingS: number };

/** Turn-by-turn state from the driver position along the route; speaks maneuvers when `voiceOn`. */
export function useGuidance(
  active: ActiveRoute | null,
  driver: LatLng,
  driving: boolean,
  voiceOn: boolean,
  say: (text: string) => void,
): Guidance {
  // Cumulative distance along the polyline, once per route.
  const cum = useMemo(() => {
    const g = active?.route.geometry ?? [];
    const out = [0];
    for (let i = 1; i < g.length; i++) {
      out.push(out[i - 1] + meters({ lat: g[i - 1][0], lng: g[i - 1][1] }, { lat: g[i][0], lng: g[i][1] }));
    }
    return out;
  }, [active?.route.geometry]);

  const state = useMemo<Guidance>(() => {
    const route = active?.route;
    if (!route || !route.geometry.length) return { next: null, toNextM: 0, remainingM: 0, remainingS: 0 };
    let best = 0;
    let bestD = Infinity;
    route.geometry.forEach(([lat, lng], i) => {
      const d = meters(driver, { lat, lng });
      if (d < bestD) {
        bestD = d;
        best = i;
      }
    });
    const total = cum[cum.length - 1] || 1;
    const remainingM = Math.max(0, total - cum[best]);
    const next = (route.maneuvers ?? []).find((m) => m.index > best) ?? null;
    const toNextM = next ? Math.max(0, cum[Math.min(next.index, cum.length - 1)] - cum[best]) : remainingM;
    return { next, toNextM, remainingM, remainingS: (route.duration_s * remainingM) / total };
  }, [active?.route, driver, cum]);

  const spoken = useRef(new Set<string>());
  useEffect(() => {
    spoken.current.clear();
  }, [active?.route]);

  useEffect(() => {
    const { next, toNextM } = state;
    if (!driving || !voiceOn || !next) return;
    const stage = STAGES.find((s) => toNextM <= s.max && toNextM >= s.min);
    if (!stage) return;
    const key = `${next.index}-${stage.key}`;
    if (spoken.current.has(key)) return;
    // Saying the far/near call also counts for the stages above it.
    STAGES.slice(0, STAGES.indexOf(stage) + 1).forEach((s) => spoken.current.add(`${next.index}-${s.key}`));
    const text = next.action === "arrive" ? next.instruction : stage.key === "now" ? next.instruction : `${spokenDistance(toNextM)}, ${next.instruction.charAt(0).toLowerCase()}${next.instruction.slice(1)}`;
    say(`${text}.`);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state, driving, voiceOn]);

  return state;
}
