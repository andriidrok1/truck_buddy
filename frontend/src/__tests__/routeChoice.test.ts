import { alternativeLabel, routesChipText, swapAlternative, switchedLine } from "../geo/routeChoice";
import type { RouteInfo } from "../types";

const base = (summary: string, extra: Partial<RouteInfo> = {}): RouteInfo => ({
  geometry: [
    [38.58, -121.49],
    [38.55, -121.42],
  ],
  distance_m: 6759,
  duration_s: 492,
  distance_mi: 4.2,
  duration_min: 8,
  summary,
  source: "here",
  ...extra,
});

const i80 = base("I-80 E", { labels: ["I-80"], tolls_usd: null, handle: "h-80" });
const us50 = base("US-50 E", { labels: ["US-50", "CA-99"], tolls_usd: 12.5, handle: "h-50" });
const unlabeled = base("Side streets", { handle: "h-side" });
const primary = base("I-5 N", { labels: ["I-5"], handle: "h-5", alternatives: [i80, us50, unlabeled] });

describe("alternativeLabel", () => {
  test("joins the engine's labels", () => {
    expect(alternativeLabel(i80, 0)).toBe("I-80");
    expect(alternativeLabel(us50, 1)).toBe("US-50 / CA-99");
  });
  test("falls back to a 1-based number", () => {
    expect(alternativeLabel(unlabeled, 2)).toBe("Alternative 3");
    expect(alternativeLabel(base("x", { labels: [] }), 0)).toBe("Alternative 1");
  });
});

describe("routesChipText", () => {
  test("counts the primary too", () => {
    expect(routesChipText(primary)).toBe("Routes (4)");
    expect(routesChipText(base("x", { alternatives: [i80] }))).toBe("Routes (2)");
  });
  test("nothing to pick from: no chip", () => {
    expect(routesChipText(i80)).toBeNull();
    expect(routesChipText(base("x", { alternatives: [] }))).toBeNull();
  });
});

describe("switchedLine", () => {
  test("names the roads, or the number without them", () => {
    expect(switchedLine(i80, 0)).toBe("Switched to the I-80 route.");
    expect(switchedLine(us50, 1)).toBe("Switched to the US-50 and CA-99 route.");
    expect(switchedLine(unlabeled, 2)).toBe("Switched to alternative 3.");
  });
});

describe("swapAlternative", () => {
  test("the pick becomes primary; the rest plus the old primary stay as alternatives", () => {
    const { route, alternatives } = swapAlternative(primary, 1);
    expect(route.handle).toBe("h-50");
    expect(route.summary).toBe("US-50 E");
    expect(route.alternatives).toBe(alternatives);
    expect(alternatives.map((a) => a.handle)).toEqual(["h-80", "h-side", "h-5"]);
    // The old primary is stored flat: no alternatives nested inside alternatives.
    expect(alternatives[2].alternatives).toBeUndefined();
    expect(alternatives[2].labels).toEqual(["I-5"]);
  });

  test("swapping back restores the original primary with the same set", () => {
    const first = swapAlternative(primary, 0);
    const back = swapAlternative(first.route, first.alternatives.length - 1);
    expect(back.route.handle).toBe("h-5");
    expect(back.route.alternatives?.map((a) => a.handle).sort()).toEqual(["h-50", "h-80", "h-side"]);
  });

  test("does not mutate the input", () => {
    const before = JSON.stringify(primary);
    swapAlternative(primary, 2);
    expect(JSON.stringify(primary)).toBe(before);
  });

  test("out of range or no alternatives: unchanged", () => {
    expect(swapAlternative(primary, 7)).toEqual({ route: primary, alternatives: primary.alternatives });
    expect(swapAlternative(i80, 0)).toEqual({ route: i80, alternatives: [] });
  });
});
