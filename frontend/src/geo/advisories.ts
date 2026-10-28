// Posted limits, speed alerts and what lies ahead on the route (incidents, NWS weather, chain
// controls). Pure: no React, no timers; the hook (useRouteAdvisories) feeds it the match and
// the clock.

import type { RouteInfo, SpeedLimit } from "../types";

/** Posted limit on the matched segment: the last `speed_limits` entry at or before it, null when unknown. */
export function limitAt(speedLimits: readonly SpeedLimit[] | undefined, matchedIndex: number): number | null {
  if (!speedLimits?.length || matchedIndex < 0) return null;
  let best: SpeedLimit | null = null;
  for (const s of speedLimits) {
    if (s.index <= matchedIndex && (!best || s.index >= best.index)) best = s;
  }
  return best ? best.mph : null;
}

export const MPH_PER_MPS = 2.23694;

export type SpeedWatchOptions = {
  /** mph over the limit before it counts. */
  overMph?: number;
  /** How long the truck must stay over before the one alert. */
  holdMs?: number;
};

export type SpeedWatch = {
  /** One fix. "alert" once the truck has been over limit+overMph for holdMs; then nothing until it drops under the limit. */
  push(fix: { speedMph: number | null; limitMph: number | null; now: number }): "alert" | null;
  reset(): void;
};

/**
 * "You're doing 70 in a 55", once: the alert fires after `holdMs` continuously over the limit
 * (a short overtake is not a lecture) and re-arms only when the truck is back under the limit.
 * A missing limit or speed cancels the hold without re-arming.
 */
export function createSpeedWatch(opts: SpeedWatchOptions = {}): SpeedWatch {
  const { overMph = 5, holdMs = 10000 } = opts;
  let overSince: number | null = null;
  let alerted = false;
  return {
    push({ speedMph, limitMph, now }) {
      if (speedMph == null || limitMph == null) {
        overSince = null;
        return null;
      }
      if (speedMph <= limitMph) {
        overSince = null;
        alerted = false;
        return null;
      }
      if (speedMph < limitMph + overMph) {
        overSince = null; // over, but within tolerance: not counting, not re-arming either
        return null;
      }
      if (alerted) return null;
      if (overSince == null) overSince = now;
      if (now - overSince < holdMs) return null;
      alerted = true;
      overSince = null;
      return "alert";
    },
    reset() {
      overSince = null;
      alerted = false;
    },
  };
}

export type AdvisoryKind = "incident" | "weather" | "chain";

export type Advisory = {
  /** Stable per route: `${kind}:${id}` is spoken once. */
  id: string;
  kind: AdvisoryKind;
  /** Short label for the chip. */
  text: string;
  /** 3 = act now (closure, severe/extreme weather, R-2/R-3), 2 = heads up, 1 = for the record. */
  severity: 1 | 2 | 3;
  /** Metres from the matched segment to it. */
  aheadM: number;
  /** Geometry index. */
  index: number;
};

export const AHEAD_MAX_M = 16000;

const SEVERE_WEATHER = new Set(["extreme", "severe"]);

const capitalize = (s: string) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : s);

const chainSeverity = (status: string): 1 | 2 | 3 => {
  const s = status.toUpperCase().replace(/\s+/g, "");
  if (s.includes("R-3") || s.includes("R3") || s.includes("R-2") || s.includes("R2")) return 3;
  if (s.includes("R-1") || s.includes("R1")) return 2;
  return 1;
};

/**
 * Everything on the route ahead of the matched segment within `aheadMaxM`, nearest first.
 * Distances are vertex to vertex (cum[index] - cum[matchedIndex]): good to a segment, which is
 * all a "4 mi" chip or a "heads up" needs.
 */
export function upcomingAdvisories(
  route: Pick<RouteInfo, "incidents" | "weather" | "chain_controls">,
  matchedIndex: number,
  cum: readonly number[],
  aheadMaxM = AHEAD_MAX_M,
): Advisory[] {
  if (matchedIndex < 0 || !cum.length) return [];
  const here = cum[Math.min(matchedIndex, cum.length - 1)];
  const ahead = (index: number) => cum[Math.min(Math.max(0, index), cum.length - 1)] - here;
  const out: Advisory[] = [];
  const push = (a: Advisory) => {
    if (a.index > matchedIndex && a.aheadM >= 0 && a.aheadM <= aheadMaxM) out.push(a);
  };
  for (const inc of route.incidents ?? []) {
    push({
      id: `${inc.index}-${inc.type}`,
      kind: "incident",
      text: inc.closed ? `Road closed: ${inc.description}` : `${capitalize(inc.type.replace(/_/g, " "))}: ${inc.description}`,
      severity: inc.closed ? 3 : 2,
      aheadM: ahead(inc.index),
      index: inc.index,
    });
  }
  for (const w of route.weather ?? []) {
    push({
      id: w.id,
      kind: "weather",
      text: w.event,
      severity: SEVERE_WEATHER.has(w.severity.toLowerCase()) ? 3 : 2,
      aheadM: ahead(w.index),
      index: w.index,
    });
  }
  for (const c of route.chain_controls ?? []) {
    push({
      id: c.id,
      kind: "chain",
      text: `Chains ${c.status} on ${c.route}, ${c.location}`,
      severity: chainSeverity(c.status),
      aheadM: ahead(c.index),
      index: c.index,
    });
  }
  return out.sort((a, b) => a.aheadM - b.aheadM || b.severity - a.severity);
}

/** "in about 4 miles" / "in half a mile" / "just ahead". */
export function spokenAhead(m: number) {
  if (m < 400) return "just ahead";
  if (m < 1200) return "in half a mile";
  const mi = Math.round(m / 1609.34);
  return mi <= 1 ? "in about a mile" : `in about ${mi} miles`;
}

/** "4 mi" / "0.5 mi" / "now" for the chip. */
export function chipAhead(m: number) {
  if (m < 150) return "now";
  const mi = m / 1609.34;
  return mi < 10 ? `in ${mi.toFixed(mi < 1 ? 1 : 0).replace(/\.0$/, "")} mi` : `in ${Math.round(mi)} mi`;
}

/** The spoken line for an advisory. */
export function advisoryText(a: Advisory): string {
  const where = spokenAhead(a.aheadM);
  switch (a.kind) {
    case "incident":
      return a.severity === 3 ? `Heads up: road closed ${where}. ${a.text.replace(/^Road closed: /, "")}.` : `Heads up: ${a.text.replace(/: /, " " + where + ": ")}.`;
    case "weather":
      return `${a.text} ${where}.`;
    case "chain":
      return `Chain control ${where}: ${a.text.replace(/^Chains /, "chains ")}.`;
  }
}
