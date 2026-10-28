import routeSet from "../../fixtures/responses/route_set.json";
import {
  announcement,
  createRouteMatcher,
  cumulativeMeters,
  FULL_SCAN_ABOVE_M,
  haversineM,
  markSpoken,
  nextManeuver,
  projectOnRoute,
  stageFor,
} from "../geo/guidance";
import type { RouteInfo } from "../types";

// Degrees of latitude per metre (and of longitude at the equator, where these synthetic routes live).
const DEG_PER_M = 1 / 111195;

/** Straight line east along the equator, `count` vertices `stepM` apart. */
const straightEast = (count: number, stepM: number): [number, number][] =>
  Array.from({ length: count }, (_, i) => [0, i * stepM * DEG_PER_M] as [number, number]);

const fixtureRoute = routeSet.route as unknown as RouteInfo;

describe("haversineM / cumulativeMeters", () => {
  test("one degree of latitude is ~111 km", () => {
    expect(haversineM({ lat: 0, lng: 0 }, { lat: 1, lng: 0 })).toBeCloseTo(111195, -2);
  });

  test("cumulative distance starts at 0 and grows by each leg", () => {
    const cum = cumulativeMeters(straightEast(4, 100));
    expect(cum).toHaveLength(4);
    expect(cum[0]).toBe(0);
    expect(cum[1]).toBeCloseTo(100, 0);
    expect(cum[3]).toBeCloseTo(300, 0);
  });

  test("fixture route sums to roughly its distance_m", () => {
    const cum = cumulativeMeters(fixtureRoute.geometry);
    expect(cum).toHaveLength(5);
    // The fixture is hand-written, so its distance_m and five-vertex geometry only agree roughly.
    expect(cum[4]).toBeGreaterThan(fixtureRoute.distance_m * 0.85);
    expect(cum[4]).toBeLessThan(fixtureRoute.distance_m * 1.15);
  });

  test("empty geometry", () => {
    expect(cumulativeMeters([])).toEqual([0]);
    expect(projectOnRoute([], [0], { lat: 0, lng: 0 }, null)).toEqual({ index: 0, t: 0, distM: Infinity, alongM: 0 });
  });
});

describe("projectOnRoute", () => {
  test("projects onto segment not vertex", () => {
    // Two vertices 1000 m apart; the truck is 50 m north of the midpoint.
    const g = straightEast(2, 1000);
    const cum = cumulativeMeters(g);
    const m = projectOnRoute(g, cum, { lat: 50 * DEG_PER_M, lng: 500 * DEG_PER_M }, null);
    expect(m.index).toBe(0);
    expect(m.t).toBeCloseTo(0.5, 2);
    expect(m.distM).toBeCloseTo(50, 0); // nearest vertex would say ~502 m
    expect(m.alongM).toBeCloseTo(500, 0);
  });

  test("beyond the end of the route clamps to the last vertex", () => {
    const g = straightEast(3, 100);
    const cum = cumulativeMeters(g);
    const m = projectOnRoute(g, cum, { lat: 0, lng: 250 * DEG_PER_M }, null);
    expect(m.index).toBe(1);
    expect(m.t).toBe(1);
    expect(m.alongM).toBeCloseTo(200, 0);
    expect(m.distM).toBeCloseTo(50, 0);
  });

  test("uses hint window then falls back when far", () => {
    // Out along the equator for 11 vertices (0..1000 m), then back 10 m further north over the same longitudes.
    const out = straightEast(11, 100);
    const back = [...out].reverse().map(([, lng]) => [10 * DEG_PER_M, lng] as [number, number]);
    const g = [...out, ...back];
    const cum = cumulativeMeters(g);
    // 4 m north of the outbound leg, 6 m south of the return leg, over the 450 m mark.
    const truck = { lat: 4 * DEG_PER_M, lng: 450 * DEG_PER_M };

    const noHint = projectOnRoute(g, cum, truck, null);
    expect(noHint.index).toBe(4); // closest overall: outbound
    expect(noHint.distM).toBeCloseTo(4, 0);

    // Hint on the return leg, window 2: the outbound leg is outside the window, and the return leg is
    // only 6 m away, so the windowed match stands even though it is not the global nearest.
    const onReturn = projectOnRoute(g, cum, truck, 16, 2);
    expect(onReturn.index).toBeGreaterThanOrEqual(11);
    expect(onReturn.distM).toBeCloseTo(6, 0);
    expect(onReturn.alongM).toBeGreaterThan(1000);

    // A stale hint whose window is more than FULL_SCAN_ABOVE_M from the truck gives up and rescans.
    const long = straightEast(200, 100); // 20 km
    const longCum = cumulativeMeters(long);
    const far = projectOnRoute(long, longCum, { lat: 0, lng: 15050 * DEG_PER_M }, 0, 5);
    expect(far.index).toBe(150);
    expect(far.t).toBeCloseTo(0.5, 2);
    expect(far.distM).toBeCloseTo(0, 0);
    // Sanity: the windowed-only answer would have been ~14.5 km off.
    expect(longCum[5] + FULL_SCAN_ABOVE_M).toBeLessThan(15050);
  });

  test("a point exactly on a vertex ties to the earlier segment", () => {
    const g = straightEast(5, 100);
    const m = projectOnRoute(g, cumulativeMeters(g), { lat: 0, lng: 200 * DEG_PER_M }, null);
    expect(m.index).toBe(1);
    expect(m.t).toBeCloseTo(1, 6);
    expect(m.alongM).toBeCloseTo(200, 0);
  });

  test("alongM increases monotonically along a straight line", () => {
    const g = straightEast(20, 50); // 950 m
    const matcher = createRouteMatcher(g, 3);
    let last = -1;
    for (let x = -20; x <= 1000; x += 7) {
      const m = matcher.match({ lat: 8 * DEG_PER_M, lng: x * DEG_PER_M }); // 8 m beside the road
      expect(m.alongM).toBeGreaterThanOrEqual(last);
      expect(m.alongM).toBeCloseTo(Math.min(950, Math.max(0, x)), -1);
      if (x >= 0 && x <= 950) expect(m.distM).toBeCloseTo(8, 0);
      last = m.alongM;
    }
    expect(matcher.hint).toBe(18); // parked on the last segment after the sweep
  });

  test("matcher keeps its hint between fixes and starts without one", () => {
    const matcher = createRouteMatcher(straightEast(5, 100));
    expect(matcher.hint).toBeNull();
    matcher.match({ lat: 0, lng: 250 * DEG_PER_M });
    expect(matcher.hint).toBe(2);
  });
});

describe("nextManeuver", () => {
  test("first maneuver strictly ahead of the matched segment", () => {
    expect(nextManeuver(fixtureRoute, 0)?.instruction).toBe("Turn right onto US-50 E");
    expect(nextManeuver(fixtureRoute, 1)?.action).toBe("exit");
    expect(nextManeuver(fixtureRoute, 2)?.action).toBe("arrive");
    expect(nextManeuver(fixtureRoute, 3)?.action).toBe("arrive");
    expect(nextManeuver(fixtureRoute, 4)).toBeNull();
    expect(nextManeuver({ maneuvers: undefined }, 0)).toBeNull();
  });
});

describe("stageFor / markSpoken", () => {
  test("stage far spoken once then near", () => {
    const spoken = new Set<string>();
    expect(stageFor(1500, 1, spoken)).toBe("far");
    markSpoken(spoken, 1, "far");
    expect(stageFor(1400, 1, spoken)).toBeNull(); // already said
    expect(stageFor(950, 1, spoken)).toBeNull(); // between stages
    expect(stageFor(800, 1, spoken)).toBe("near");
    markSpoken(spoken, 1, "near");
    expect(stageFor(300, 1, spoken)).toBeNull();
    expect(stageFor(100, 1, spoken)).toBeNull(); // 90..120 gap
    expect(stageFor(50, 1, spoken)).toBe("now");
    markSpoken(spoken, 1, "now");
    expect(stageFor(0, 1, spoken)).toBeNull();
    // Another maneuver has its own slots.
    expect(stageFor(1500, 2, spoken)).toBe("far");
  });

  test("a near call also covers far, so a far call never follows it", () => {
    const spoken = new Set<string>();
    expect(stageFor(500, 3, spoken)).toBe("near");
    markSpoken(spoken, 3, "near");
    expect(stageFor(1500, 3, spoken)).toBeNull();
    expect(stageFor(50, 3, spoken)).toBe("now");
  });

  test("out of band distances give nothing", () => {
    expect(stageFor(5000, 0, new Set())).toBeNull();
    expect(stageFor(1900, 0, new Set())).toBeNull();
  });
});

describe("announcement", () => {
  const turn = { action: "turn", instruction: "Turn right onto US-50 E" };
  const arrive = { action: "arrive", instruction: "Arrive at Pilot Travel Center on the right" };

  test("lead-in by distance, instruction lower-cased", () => {
    expect(announcement(turn, "far", 1500)).toBe("In about a mile, turn right onto US-50 E.");
    expect(announcement(turn, "near", 800)).toBe("In half a mile, turn right onto US-50 E.");
    expect(announcement(turn, "near", 300)).toBe("In 500 feet, turn right onto US-50 E.");
    expect(announcement(turn, "now", 40)).toBe("Turn right onto US-50 E.");
  });

  test("arrive uses raw instruction", () => {
    expect(announcement(arrive, "far", 1500)).toBe("Arrive at Pilot Travel Center on the right.");
    expect(announcement(arrive, "near", 500)).toBe("Arrive at Pilot Travel Center on the right.");
  });
});
