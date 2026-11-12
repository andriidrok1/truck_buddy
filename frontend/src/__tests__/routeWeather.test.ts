import { useLangStore } from "../i18n";
import { reduceActions } from "../state/agentReducer";
import type { AppAction, WeatherAlert } from "../types";
import {
  alertAt, cardLine, chipMode, chips, chipValue, detail, DETAIL_ZOOM, samplePoints, zoomOf, type RoutePoint,
} from "../weather/routeWeather";

// Sacramento -> Reno along I-80, roughly.
const LINE: [number, number][] = [
  [38.58, -121.49], [38.75, -121.27], [38.9, -121.07], [39.1, -120.95], [39.28, -120.7],
  [39.32, -120.33], [39.33, -120.18], [39.49, -120.0], [39.53, -119.81],
];
const NOW = new Date("2026-11-11T22:00:00Z");

function point(over: Partial<RoutePoint>): RoutePoint {
  return { index: 0, lat: 39, lng: -120, eta: "2026-11-11T23:50:00Z", kind: "clear", severity: "info", ice_risk: false, reasons: [], ...over } as RoutePoint;
}

afterEach(() => useLangStore.setState({ lang: "en" }));

describe("samplePoints", () => {
  it("keeps both ends and spreads the ETA by distance", () => {
    const s = samplePoints(LINE, 3 * 3600, NOW, 40);
    expect(s[0]).toMatchObject({ index: 0, eta: NOW.toISOString() });
    expect(s[s.length - 1]).toMatchObject({ index: LINE.length - 1, eta: "2026-11-12T01:00:00.000Z" });
    const times = s.map((p) => Date.parse(p.eta));
    expect([...times].sort()).toEqual(times);
    expect(s.length).toBeGreaterThan(2);
  });

  it("never sends more than the backend takes", () => {
    const long: [number, number][] = Array.from({ length: 400 }, (_, i) => [35 + i * 0.01, -120 + i * 0.03]);
    expect(samplePoints(long, 20 * 3600, NOW).length).toBeLessThanOrEqual(14);
  });

  it("handles an empty or one-point route", () => {
    expect(samplePoints([], 100, NOW)).toEqual([]);
    expect(samplePoints([[38, -121]], 100, NOW)).toHaveLength(1);
  });
});

describe("chips", () => {
  const alert = { id: "a", event: "Winter Storm Warning", severity: "Severe", headline: "", area: "Greater Lake Tahoe Area", index: 4, index_end: 6 } as WeatherAlert;
  const route = { weather: [alert], geometry: LINE };

  it("a point inside an NWS alert is a warning even if the forecast looks calm", () => {
    const [c] = chips([point({ index: 5 })], route);
    expect(c.warn).toBe(true);
    expect(c.alert?.event).toBe("Winter Storm Warning");
    expect(alertAt({ index: 7 }, [alert], 8)).toBeNull();
    expect(alertAt({ index: 8 }, [{ ...alert, index_end: null }], 8)).not.toBeNull(); // unknown end = to the route end
  });

  it("only what matters stays as a chip when zoomed out", () => {
    const [calm, ice] = chips([point({ index: 1 }), point({ index: 2, severity: "warn", kind: "ice", reasons: ["ice"] })], { weather: [], geometry: LINE });
    expect(chipMode(calm, DETAIL_ZOOM - 1)).toBe("dot");
    expect(chipMode(calm, DETAIL_ZOOM)).toBe("chip");
    expect(chipMode(ice, 5)).toBe("chip");
  });

  it("shows gusts on a wind chip, the temperature otherwise", () => {
    expect(chipValue(point({ kind: "wind", gust_mph: 60, temp_f: 41 }))).toBe("60");
    expect(chipValue(point({ kind: "snow", temp_f: 27 }))).toBe("27°");
  });

  it("zoom from a region", () => {
    expect(Math.round(zoomOf(360, 256))).toBe(0);
    expect(Math.round(zoomOf(2.3, 412))).toBe(8);
  });
});

describe("card line and details", () => {
  const clock = () => "3:50 PM";
  const list = chips([point({ index: 1 }), point({ index: 5, kind: "ice", severity: "warn", reasons: ["ice"], place: "Truckee, CA" })], { weather: [], geometry: LINE });

  it("names the first point that matters", () => {
    expect(cardLine(list, clock)).toEqual({ kind: "ice", title: "Ice risk near Truckee around 3:50 PM", sub: "NWS forecast for when you get there" });
    expect(cardLine(chips([point({})], { weather: [], geometry: LINE }), clock)).toBeNull();
  });

  it("in Russian", () => {
    useLangStore.setState({ lang: "ru" });
    expect(cardLine(list, clock)?.title).toBe("Риск гололёда у Truckee около 3:50 PM");
    expect(detail({ ...list[1], gust_mph: 35, vis_mi: 0.5, kind: "fog" })).toBe("Туман · порывы 35 миль/ч · видимость 0,5 мили");
  });
});

describe("empty trailer by voice", () => {
  it("goes into the truck profile", () => {
    const fx = reduceActions([{ type: "set_trailer_empty", value: true } as AppAction], { activePlaceId: null, optionsCount: 0, favoriteChains: [] });
    expect(fx).toContainEqual(expect.objectContaining({ type: "saveProfile", patch: expect.objectContaining({ trailer_empty: true }) }));
  });
});
