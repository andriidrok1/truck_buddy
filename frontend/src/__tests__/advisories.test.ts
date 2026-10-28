import { advisoryText, chipAhead, createSpeedWatch, limitAt, spokenAhead, upcomingAdvisories, type Advisory } from "../geo/advisories";
import { cumulativeMeters } from "../geo/guidance";
import type { RouteInfo } from "../types";

const T0 = 1_700_000_000_000;
const DEG_PER_M = 1 / 111195;
// 21 vertices 1 km apart: cum[i] = i * 1000 m (near enough).
const geometry: [number, number][] = Array.from({ length: 21 }, (_, i) => [0, i * 1000 * DEG_PER_M]);
const cum = cumulativeMeters(geometry);

describe("limitAt", () => {
  const limits = [
    { index: 0, mph: 35 },
    { index: 4, mph: 65 },
    { index: 12, mph: 55 },
  ];
  test("last entry at or before the matched segment", () => {
    expect(limitAt(limits, 0)).toBe(35);
    expect(limitAt(limits, 3)).toBe(35);
    expect(limitAt(limits, 4)).toBe(65);
    expect(limitAt(limits, 11)).toBe(65);
    expect(limitAt(limits, 12)).toBe(55);
    expect(limitAt(limits, 500)).toBe(55);
  });
  test("unknown without data, before the first entry, or without a match", () => {
    expect(limitAt(undefined, 3)).toBeNull();
    expect(limitAt([], 3)).toBeNull();
    expect(limitAt([{ index: 5, mph: 65 }], 2)).toBeNull();
    expect(limitAt(limits, -1)).toBeNull();
  });
  test("order of entries does not matter", () => {
    expect(limitAt([limits[2], limits[0], limits[1]], 8)).toBe(65);
  });
});

describe("createSpeedWatch", () => {
  const run = (w: ReturnType<typeof createSpeedWatch>, speeds: (number | null)[], limit: number | null = 55, stepMs = 1000) =>
    speeds.map((speedMph, i) => w.push({ speedMph, limitMph: limit, now: T0 + i * stepMs }));

  test("alerts once after 10 s more than 5 mph over, then stays quiet", () => {
    const w = createSpeedWatch();
    const out = run(w, Array(15).fill(70));
    expect(out.indexOf("alert")).toBe(10);
    expect(out.filter((v) => v === "alert")).toHaveLength(1);
    expect(run(w, Array(30).fill(75), 55).includes("alert")).toBe(false);
  });

  test("within 5 mph over does not count; dipping into the tolerance restarts the hold", () => {
    const w = createSpeedWatch();
    expect(run(w, Array(30).fill(59)).includes("alert")).toBe(false);
    expect(run(w, [70, 70, 70, 70, 70, 58, 70, 70, 70, 70, 70, 70, 70, 70, 70, 70, 70]).indexOf("alert")).toBe(16);
  });

  test("re-arms only under the limit", () => {
    const w = createSpeedWatch({ overMph: 5, holdMs: 2000 });
    expect(run(w, [70, 70, 70])).toEqual([null, null, "alert"]);
    expect(run(w, [57, 70, 70, 70])).toEqual([null, null, null, null]); // 57 is over the limit: still armed off
    expect(run(w, [55, 70, 70, 70])).toEqual([null, null, null, "alert"]);
  });

  test("missing limit or speed cancels the hold without re-arming; reset re-arms", () => {
    const w = createSpeedWatch({ holdMs: 2000 });
    expect(run(w, [70, 70, 70])).toEqual([null, null, "alert"]);
    expect(run(w, [70, 70], null)).toEqual([null, null]);
    expect(run(w, [null, 70, 70, 70])).toEqual([null, null, null, null]);
    w.reset();
    expect(run(w, [70, 70, 70])).toEqual([null, null, "alert"]);
    const fresh = createSpeedWatch({ holdMs: 2000 });
    expect(run(fresh, [70, null, 70, 70, 70])).toEqual([null, null, null, null, "alert"]); // hold restarted at index 2
  });
});

describe("upcomingAdvisories", () => {
  const route: Pick<RouteInfo, "incidents" | "weather" | "chain_controls"> = {
    incidents: [
      { type: "road_closure", closed: true, description: "US-50 closed at Echo Summit", index: 8, criticality: "critical" },
      { type: "construction", closed: false, description: "Lane closed near Placerville", index: 3, criticality: "minor" },
      { type: "accident", closed: false, description: "Behind us", index: 1, criticality: "major" },
    ],
    weather: [
      { id: "nws-1", event: "Winter Storm Warning", severity: "Severe", headline: "Heavy snow above 6000 ft", area: "Sierra Nevada", index: 10 },
      { id: "nws-2", event: "Wind Advisory", severity: "Moderate", headline: "Gusts to 45 mph", area: "Sacramento Valley", index: 5 },
      { id: "nws-3", event: "Dense Fog Advisory", severity: "Minor", headline: "", area: "Valley", index: 19 },
    ],
    chain_controls: [
      { id: "ch-1", route: "US-50", location: "Twin Bridges", status: "R-2", index: 12, lat: 0, lng: 0 },
      { id: "ch-2", route: "US-50", location: "Pollock Pines", status: "R-1", index: 6, lat: 0, lng: 0 },
      { id: "ch-3", route: "US-50", location: "Meyers", status: "Lifted", index: 14, lat: 0, lng: 0 },
    ],
  };

  test("ahead of the match, within 16 km, nearest first, with severities", () => {
    const out = upcomingAdvisories(route, 2, cum);
    expect(out.map((a) => [a.kind, a.id, a.severity, Math.round(a.aheadM / 1000)])).toEqual([
      ["incident", "3-construction", 2, 1],
      ["weather", "nws-2", 2, 3],
      ["chain", "ch-2", 2, 4],
      ["incident", "8-road_closure", 3, 6],
      ["weather", "nws-1", 3, 8],
      ["chain", "ch-1", 3, 10],
      ["chain", "ch-3", 1, 12],
    ]);
    // nws-3 at 17 km is beyond the window; the accident at index 1 is behind.
    expect(out.find((a) => a.id === "nws-3")).toBeUndefined();
    expect(out.find((a) => a.id === "1-accident")).toBeUndefined();
  });

  test("the window moves with the truck", () => {
    expect(upcomingAdvisories(route, 7, cum).map((a) => a.id)).toEqual(["8-road_closure", "nws-1", "ch-1", "ch-3", "nws-3"]);
    expect(upcomingAdvisories(route, 19, cum)).toEqual([]);
    expect(upcomingAdvisories(route, 2, cum, 3500).map((a) => a.id)).toEqual(["3-construction", "nws-2"]);
  });

  test("chip texts", () => {
    const out = upcomingAdvisories(route, 2, cum);
    expect(out[0].text).toBe("Construction: Lane closed near Placerville");
    expect(out[3].text).toBe("Road closed: US-50 closed at Echo Summit");
    expect(out[5].text).toBe("Chains R-2 on US-50, Twin Bridges");
  });

  test("nothing without a match or data", () => {
    expect(upcomingAdvisories(route, -1, cum)).toEqual([]);
    expect(upcomingAdvisories({}, 2, cum)).toEqual([]);
    expect(upcomingAdvisories(route, 2, [])).toEqual([]);
  });
});

describe("spoken / chip distances", () => {
  test("spokenAhead", () => {
    expect(spokenAhead(100)).toBe("just ahead");
    expect(spokenAhead(800)).toBe("in half a mile");
    expect(spokenAhead(1700)).toBe("in about a mile");
    expect(spokenAhead(6400)).toBe("in about 4 miles");
  });
  test("chipAhead", () => {
    expect(chipAhead(50)).toBe("now");
    expect(chipAhead(800)).toBe("in 0.5 mi");
    expect(chipAhead(6400)).toBe("in 4 mi");
    expect(chipAhead(20000)).toBe("in 12 mi");
  });
});

describe("advisoryText", () => {
  const a = (over: Partial<Advisory>): Advisory => ({ id: "x", kind: "weather", text: "", severity: 2, aheadM: 6400, index: 5, ...over });
  test("closure, incident, weather, chains", () => {
    expect(advisoryText(a({ kind: "incident", severity: 3, text: "Road closed: US-50 closed at Echo Summit" }))).toBe(
      "Heads up: road closed in about 4 miles. US-50 closed at Echo Summit.",
    );
    expect(advisoryText(a({ kind: "incident", text: "Construction: Lane closed near Placerville", aheadM: 800 }))).toBe(
      "Heads up: Construction in half a mile: Lane closed near Placerville.",
    );
    expect(advisoryText(a({ kind: "weather", text: "Winter Storm Warning" }))).toBe("Winter Storm Warning in about 4 miles.");
    expect(advisoryText(a({ kind: "chain", text: "Chains R-2 on US-50, Twin Bridges", aheadM: 200 }))).toBe(
      "Chain control just ahead: chains R-2 on US-50, Twin Bridges.",
    );
  });
});
