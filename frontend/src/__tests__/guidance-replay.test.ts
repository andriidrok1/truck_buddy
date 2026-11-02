// The guidance chain over whole traces: what the driver would hear and when the truck would be called
// off the route. Synthetic drives today; a recorded drive goes into fixtures/traces/ and gets the same
// checks (a false "off" on a real recording is a bug in offroute.ts first, a threshold here second).
import routeSet from "../../fixtures/responses/route_set.json";
import sacReno from "../../fixtures/routes/sac_reno.json";
import { STAGES } from "../geo/guidance";
import { replayGuidance } from "../geo/replay";
import { syntheticTrace } from "../geo/trace";
import type { RouteInfo } from "../types";

const SAC_RENO = sacReno as unknown as RouteInfo;
const SHORT = (routeSet as unknown as { route: RouteInfo }).route;

/** A "now" call must come with at least this much road left to the turn... */
const NOW_MAX_M = STAGES.find((s) => s.key === "now")!.max; // 90
/** ...and never earlier than this (the plan's bar for a real recording is 150 m). */
const NOW_EARLIEST_M = 150;
/** Maneuvers this close to the previous one may share its call. */
const CLOSE_MANEUVER_M = 90;
/** The detour's "off" must land within this of the moment the truck left the line. */
const OFF_WINDOW_MS = 30_000;
const HIGHWAY_MPS = 29; // 65 mph
const CITY_MPS = 13;

const band = (key: "far" | "near" | "now") => STAGES.find((s) => s.key === key)!;

describe("Sacramento -> Reno (754 vertices, 9 maneuvers)", () => {
  const clean = syntheticTrace(SAC_RENO.geometry, HIGHWAY_MPS);

  test("a clean drive is never off the route", () => {
    const r = replayGuidance(SAC_RENO, clean);
    expect(r.offRouteAt).toEqual([]);
    expect(r.verdicts.filter((v) => v.verdict === "on").length).toBeGreaterThan(clean.length * 0.9);
  });

  test("leaving the line for 150 m is one 'off', within the window", () => {
    const detourAt = 2000; // fixes (seconds) into the drive
    const trace = syntheticTrace(SAC_RENO.geometry, HIGHWAY_MPS, { detourAtIndex: detourAt, detourM: 150, detourPoints: 25 });
    const r = replayGuidance(SAC_RENO, trace);
    expect(r.offRouteAt).toHaveLength(1);
    expect(Math.abs(r.offRouteAt[0] - detourAt * 1000)).toBeLessThanOrEqual(OFF_WINDOW_MS);
  });

  test("every maneuver gets its 'now' call in the right place, far and near calls in their bands", () => {
    const r = replayGuidance(SAC_RENO, clean);
    const maneuvers = SAC_RENO.maneuvers ?? [];
    for (let i = 0; i < maneuvers.length; i++) {
      const m = maneuvers[i];
      const along = r.maneuverAlongM[i];
      const gapBefore = i === 0 ? along : along - r.maneuverAlongM[i - 1];
      const now = r.announcements.find((a) => a.maneuverIndex === m.index && a.stage === "now");
      if (gapBefore < CLOSE_MANEUVER_M && !now) continue; // right on top of the previous turn: may share its call
      expect(now).toBeDefined();
      expect(now!.toNextM).toBeLessThanOrEqual(NOW_MAX_M);
      expect(now!.toNextM).toBeLessThanOrEqual(NOW_EARLIEST_M);
      expect(now!.alongM).toBeLessThanOrEqual(along + 1); // not after passing the vertex
    }
    for (const a of r.announcements) {
      if (a.stage === "now") continue;
      const b = band(a.stage);
      expect(a.toNextM).toBeGreaterThanOrEqual(b.min);
      expect(a.toNextM).toBeLessThanOrEqual(b.max);
    }
    // The long I-80 leg gets the full far / near / now sequence for the exit at its end.
    const exit = maneuvers.findIndex((m) => m.index === 671);
    expect(exit).toBeGreaterThan(0);
    const stages = r.announcements.filter((a) => a.maneuverIndex === 671).map((a) => a.stage);
    expect(stages).toEqual(["far", "near", "now"]);
  });

  test("a stop on the shoulder is not an off-route", () => {
    const trace = syntheticTrace(SAC_RENO.geometry, HIGHWAY_MPS, { stopAtIndex: 3000, stopS: 120 });
    expect(replayGuidance(SAC_RENO, trace).offRouteAt).toEqual([]);
  });
});

describe("the short city route (fixtures/responses/route_set.json)", () => {
  test("announces each turn once per stage and arrives", () => {
    const trace = syntheticTrace(SHORT.geometry, CITY_MPS);
    const r = replayGuidance(SHORT, trace);
    expect(r.offRouteAt).toEqual([]);
    const keys = r.announcements.map((a) => `${a.maneuverIndex}-${a.stage}`);
    expect(new Set(keys).size).toBe(keys.length); // never the same stage twice
    const last = (SHORT.maneuvers ?? [])[SHORT.maneuvers!.length - 1];
    expect(r.announcements.some((a) => a.maneuverIndex === last.index && a.stage === "now")).toBe(true);
  });
});
