// Off-route detection on top of projectOnRoute's perpendicular distance. Pure: no React, no timers,
// every rule takes the fix's own timestamp so a replayed trace behaves like live GPS.

/**
 * How far from the route line counts as "off". Highways get 50 m: the match can sit on the wrong
 * carriageway of a divided road, and 40 m is a normal GPS error at 70 mph. Otherwise 30 m (a city
 * block's parallel street is ~100 m away, a parking lane ~10 m). Without a posted limit, highway
 * speed itself (> 20 m/s, ~45 mph) picks the wider band.
 */
export function thresholdM(limitMph: number | null, speedMps: number | null): number {
  if (limitMph != null) return limitMph >= 50 ? 50 : 30;
  return speedMps != null && speedMps > 20 ? 50 : 30;
}

export type OffRouteFix = {
  /** Perpendicular distance from the route, metres (RouteMatch.distM). */
  distM: number;
  /** m/s; null when the platform does not report speed (counts as moving). */
  speed: number | null;
  /** Horizontal accuracy radius, metres; null when unknown (counts as good). */
  accuracy: number | null;
  /** Fix time, epoch ms. */
  ts: number;
  /** thresholdM() for this fix. */
  thresholdM: number;
};

export type OffRouteVerdict = "on" | "off" | "skip";

export type OffRouteTrackerOptions = {
  /** Consecutive fixes over the threshold before "off". */
  need?: number;
  /** After an "off", no second "off" for this long (a reroute in flight, or one that failed). */
  cooldownMs?: number;
  /** Fixes with a worse accuracy radius are ignored. */
  maxAccuracyM?: number;
  /** Below this speed the truck is parked (lot, shoulder): a parked truck is never "off". */
  minSpeedMps?: number;
  /** Implied speed (change of distM over time) above this is a GPS jump, not driving. */
  maxJumpMps?: number;
  /** No fix for longer than this (tunnel, app suspended): start counting from scratch. */
  maxGapMs?: number;
};

export type OffRouteTracker = {
  /** Judges one fix. The same fix (same `ts`) pushed again returns the first verdict without counting twice. */
  push(fix: OffRouteFix): OffRouteVerdict;
  /** Forget the count and the cooldown: new route, or a reroute just landed. */
  reset(): void;
};

export function createOffRouteTracker(opts: OffRouteTrackerOptions = {}): OffRouteTracker {
  const { need = 3, cooldownMs = 30000, maxAccuracyM = 40, minSpeedMps = 2, maxJumpMps = 60, maxGapMs = 15000 } = opts;
  let count = 0;
  let cooldownUntil = 0;
  /** Last fix of any kind that reached the gap check (proves the receiver is alive). */
  let lastTs: number | null = null;
  /** Last fix that was judged (on or off), for the implied-speed check. */
  let last: { distM: number; ts: number } | null = null;
  /** The previous push, replayed when a render pushes the same fix twice (memo re-run, strict mode). */
  let lastPush: { ts: number; verdict: OffRouteVerdict } | null = null;

  const judge = (fix: OffRouteFix): OffRouteVerdict => {
    if (fix.accuracy != null && fix.accuracy > maxAccuracyM) return "skip";
    if (fix.speed !== null && fix.speed < minSpeedMps) {
      lastTs = fix.ts; // parked fixes keep the clock: no "tunnel" when the truck pulls out again
      return "skip";
    }
    const gap = lastTs != null && fix.ts - lastTs > maxGapMs;
    lastTs = fix.ts;
    if (gap) {
      count = 0;
      last = { distM: fix.distM, ts: fix.ts };
      return "skip";
    }
    if (last && fix.ts > last.ts) {
      const implied = Math.abs(fix.distM - last.distM) / ((fix.ts - last.ts) / 1000);
      if (implied > maxJumpMps) {
        // The jump becomes the reference: a real teleport (replay) is accepted on the next fix,
        // a one-off glitch is skipped again on the way back and then forgotten.
        last = { distM: fix.distM, ts: fix.ts };
        return "skip";
      }
    }
    last = { distM: fix.distM, ts: fix.ts };
    if (fix.distM <= fix.thresholdM) {
      count = 0;
      return "on";
    }
    count = Math.min(need, count + 1);
    if (count < need) return "skip";
    // Still off during the cooldown: the count stays saturated, so the first fix after the
    // cooldown fires again without waiting for `need` more.
    if (fix.ts < cooldownUntil) return "skip";
    cooldownUntil = fix.ts + cooldownMs;
    count = 0;
    return "off";
  };

  return {
    push(fix) {
      if (lastPush && lastPush.ts === fix.ts) return lastPush.verdict;
      const verdict = judge(fix);
      lastPush = { ts: fix.ts, verdict };
      return verdict;
    },
    reset() {
      count = 0;
      cooldownUntil = 0;
      last = null;
      lastTs = null;
      lastPush = null;
    },
  };
}
