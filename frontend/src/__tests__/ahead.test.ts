import { AHEAD_MAX_POINTS, routePointsAhead } from "../geo/ahead";
import { cumulativeMeters } from "../geo/guidance";

const DEG_PER_M = 1 / 111195;
const M_PER_MI = 1609.34;

/** Straight line north from the equator, `count` vertices `stepMi` miles apart. */
const straightNorth = (count: number, stepMi: number): [number, number][] =>
  Array.from({ length: count }, (_, i) => [i * stepMi * M_PER_MI * DEG_PER_M, 0] as [number, number]);

describe("routePointsAhead", () => {
  // 100 vertices one mile apart = a 99-mile route.
  const geometry = straightNorth(100, 1);
  const cum = cumulativeMeters(geometry);

  test("one point every ~5 miles up to 60, nearest first, after the matched point", () => {
    const matched = 10;
    const alongM = cum[matched] + 800; // half a mile into segment 10
    const pts = routePointsAhead(geometry, cum, matched, alongM);
    expect(pts).toHaveLength(12);
    pts.forEach(([lat, lng, mi], k) => {
      expect(lng).toBe(0);
      expect(lat).toBeGreaterThan(geometry[matched][0]); // strictly ahead of the matched vertex
      // The first vertex at or past k*5 miles: between the target and the target + one vertex spacing.
      expect(mi).toBeGreaterThanOrEqual((k + 1) * 5);
      expect(mi).toBeLessThanOrEqual((k + 1) * 5 + 1.01);
      if (k > 0) expect(mi).toBeGreaterThan(pts[k - 1][2]);
    });
    expect(pts[pts.length - 1][2]).toBeLessThanOrEqual(61);
  });

  test("stops at the end of the route", () => {
    const matched = 90; // 9 miles left
    const pts = routePointsAhead(geometry, cum, matched, cum[matched]);
    expect(pts).toHaveLength(1);
    expect(pts[0][2]).toBeCloseTo(5, 1);
    // Under one step left: nothing.
    expect(routePointsAhead(geometry, cum, 96, cum[96])).toEqual([]);
  });

  test("caps at the backend's limit", () => {
    const pts = routePointsAhead(geometry, cum, 0, 0, 1, 99);
    expect(pts).toHaveLength(AHEAD_MAX_POINTS);
  });

  test("nothing without a match or a route", () => {
    expect(routePointsAhead(geometry, cum, -1, 0)).toEqual([]);
    expect(routePointsAhead([], [0], 0, 0)).toEqual([]);
    expect(routePointsAhead(geometry, [0], 0, 0)).toEqual([]); // cum from another route
  });
});
