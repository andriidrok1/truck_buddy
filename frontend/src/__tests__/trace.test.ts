import routeSet from "../../fixtures/responses/route_set.json";
import { cumulativeMeters, haversineM, projectOnRoute } from "../geo/guidance";
import { createOffRouteTracker, thresholdM } from "../geo/offroute";
import { bearingDeg, offsetM, syntheticTrace } from "../geo/trace";
import type { RouteInfo } from "../types";

const DEG_PER_M = 1 / 111195;
const straightEast = (count: number, stepM: number): [number, number][] =>
  Array.from({ length: count }, (_, i) => [0, i * stepM * DEG_PER_M] as [number, number]);

const fixtureRoute = routeSet.route as unknown as RouteInfo;

describe("bearingDeg / offsetM", () => {
  test("cardinal bearings", () => {
    expect(bearingDeg([0, 0], [1, 0])).toBeCloseTo(0, 5);
    expect(bearingDeg([0, 0], [0, 1])).toBeCloseTo(90, 5);
    expect(bearingDeg([1, 0], [0, 0])).toBeCloseTo(180, 5);
    expect(bearingDeg([0, 1], [0, 0])).toBeCloseTo(270, 5);
  });

  test("offsetM moves the given metres in the given direction", () => {
    const p: [number, number] = [38.5, -121.5];
    const east = offsetM(p, 90, 100);
    expect(haversineM({ lat: p[0], lng: p[1] }, { lat: east[0], lng: east[1] })).toBeCloseTo(100, 0);
    expect(east[0]).toBeCloseTo(p[0], 9);
    expect(east[1]).toBeGreaterThan(p[1]);
    const north = offsetM(p, 0, 100);
    expect(north[0]).toBeGreaterThan(p[0]);
    expect(haversineM({ lat: p[0], lng: p[1] }, { lat: north[0], lng: north[1] })).toBeCloseTo(100, 0);
  });
});

describe("syntheticTrace", () => {
  test("walks the line at the given speed, one fix per stepS", () => {
    const g = straightEast(11, 100); // 1000 m
    const tr = syntheticTrace(g, 25);
    expect(tr).toHaveLength(41); // 0, 25, ..., 1000
    expect(tr[0]).toMatchObject({ lat: 0, lng: 0, speed: 25, accuracy: 5, t_ms: 0 });
    expect(tr[40].t_ms).toBe(40000);
    expect(tr[40].lng).toBeCloseTo(1000 * DEG_PER_M, 7);
    expect(tr[7].heading).toBeCloseTo(90, 3);
    expect(tr[7].lng).toBeCloseTo(175 * DEG_PER_M, 7);
    const cum = cumulativeMeters(g);
    tr.forEach((p) => expect(projectOnRoute(g, cum, p, null).distM).toBeLessThan(0.01));
  });

  test("stepS 2 halves the fix count, a partial last step still ends on the last vertex, empty inputs give nothing", () => {
    expect(syntheticTrace(straightEast(11, 100), 25, { stepS: 2 })).toHaveLength(21);
    const odd = syntheticTrace(straightEast(11, 100), 30); // 33.3 steps
    expect(odd).toHaveLength(35);
    expect(odd[34].lng).toBeCloseTo(1000 * DEG_PER_M, 7);
    expect(odd[33].lng).toBeCloseTo(990 * DEG_PER_M, 7);
    expect(syntheticTrace([], 25)).toEqual([]);
    expect(syntheticTrace(straightEast(3, 100), 0)).toEqual([]);
  });

  test("the detour shifts whole fixes to the right of travel and comes back", () => {
    const g = straightEast(11, 100);
    const cum = cumulativeMeters(g);
    const tr = syntheticTrace(g, 20, { detourAtIndex: 10, detourM: 120, detourPoints: 5 });
    expect(tr).toHaveLength(51);
    tr.forEach((p, i) => {
      const d = projectOnRoute(g, cum, p, null).distM;
      if (i >= 10 && i < 15) {
        expect(d).toBeCloseTo(120, 0);
        expect(p.lat).toBeLessThan(0); // heading east, right = south
      } else expect(d).toBeLessThan(0.01);
    });
    expect(tr[12].t_ms).toBe(12000); // the detour costs no time
  });

  test("the stop repeats the spot with speed 0 and pushes later fixes out in time", () => {
    const g = straightEast(11, 100);
    const tr = syntheticTrace(g, 20, { stopAtIndex: 5, stopS: 10 });
    expect(tr).toHaveLength(51 + 10);
    for (let i = 5; i < 15; i++) {
      expect(tr[i].speed).toBe(0);
      expect(tr[i].lat).toBe(tr[15].lat);
      expect(tr[i].lng).toBe(tr[15].lng);
      expect(tr[i].t_ms).toBe(i * 1000);
    }
    expect(tr[15]).toMatchObject({ speed: 20, t_ms: 15000 });
    expect(tr[60].t_ms).toBe(60000);
  });

  test("fixture route: headings follow the bends", () => {
    const tr = syntheticTrace(fixtureRoute.geometry, 27);
    expect(tr.length).toBeGreaterThan(200);
    const first = bearingDeg(fixtureRoute.geometry[0], fixtureRoute.geometry[1]);
    expect(tr[1].heading).toBeCloseTo(first, 3);
    const end = tr[tr.length - 1];
    expect(end.lat).toBeCloseTo(fixtureRoute.geometry[4][0], 9);
    expect(end.lng).toBeCloseTo(fixtureRoute.geometry[4][1], 9);
    expect(end.t_ms).toBe((tr.length - 1) * 1000);
  });
});

describe("replay through projectOnRoute + tracker", () => {
  /** Runs a trace through the same pipeline as useGuidance: match, threshold, tracker. */
  const replay = (geometry: readonly (readonly [number, number])[], trace: ReturnType<typeof syntheticTrace>) => {
    const cum = cumulativeMeters(geometry);
    const tracker = createOffRouteTracker();
    let hint: number | null = null;
    const t0 = 1_700_000_000_000;
    return trace.map((p) => {
      const m = projectOnRoute(geometry, cum, p, hint);
      hint = m.index;
      return tracker.push({ distM: m.distM, speed: p.speed, accuracy: p.accuracy, ts: t0 + p.t_ms, thresholdM: thresholdM(null, p.speed) });
    });
  };

  test("the missed-exit trace gives exactly one 'off', right after the detour starts", () => {
    const g = fixtureRoute.geometry;
    const detourAt = 60;
    const trace = syntheticTrace(g, 27, { detourAtIndex: detourAt, detourM: 150, detourPoints: 25 });
    const verdicts = replay(g, trace);
    const offs = verdicts.map((v, i) => (v === "off" ? i : -1)).filter((i) => i >= 0);
    expect(offs).toHaveLength(1);
    // The first detour fix is a jump (skipped), the next three count.
    expect(offs[0]).toBeGreaterThanOrEqual(detourAt + 3);
    expect(offs[0]).toBeLessThanOrEqual(detourAt + 4);
    // Back on the line afterwards, and nothing before the detour.
    expect(verdicts.slice(0, detourAt).every((v) => v === "on")).toBe(true);
    expect(verdicts[detourAt + 30]).toBe("on");
  });

  test("the clean trace and the stop trace never go off", () => {
    const g = fixtureRoute.geometry;
    expect(replay(g, syntheticTrace(g, 27)).includes("off")).toBe(false);
    const stopped = replay(g, syntheticTrace(g, 27, { stopAtIndex: 100, stopS: 90 }));
    expect(stopped.includes("off")).toBe(false);
    expect(stopped.slice(100, 190).every((v) => v === "skip")).toBe(true); // parked
    expect(stopped[195]).toBe("on");
  });
});
