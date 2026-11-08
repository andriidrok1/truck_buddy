import { routeFacts, routeTag } from "../geo/routeChoice";
import { parseMpg } from "../truck/validate";
import type { RouteInfo } from "../types";

const base: RouteInfo = { geometry: [], distance_m: 212000, duration_s: 10320, distance_mi: 132, duration_min: 172, summary: "", source: "here" };

describe("route cost on the Routes sheet", () => {
  it("shows the tag first and the estimate last", () => {
    expect(routeFacts({ ...base, tags: ["cheapest"], cost_usd: 251.1 })).toBe("Cheapest · 2 h 52 min · 132 mi · ~$251");
    expect(routeFacts({ ...base, tags: ["fastest"], tolls_usd: 120, cost_usd: 282.6 })).toBe("Fastest · 2 h 52 min · 132 mi · $120.00 tolls · ~$283");
  });
  it("says nothing it does not know", () => {
    expect(routeFacts(base)).toBe("2 h 52 min · 132 mi");
    expect(routeTag({ ...base, tags: [] })).toBeNull();
  });
});

describe("parseMpg", () => {
  it("empty is the default, out of range is invalid", () => {
    expect(parseMpg("")).toBeNull();
    expect(parseMpg(" 7,25 ")).toBe(7.3);
    expect(parseMpg("2")).toBeUndefined();
    expect(parseMpg("abc")).toBeUndefined();
  });
});
