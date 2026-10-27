import { cumulativeMeters } from "../geo/guidance";
import { backOnRoute, bearingDeg, compassOf, createNetAnnouncer, NET_HOLD_MS, spokenMiles } from "../geo/offlineGuidance";

const T0 = 1_700_000_000_000;

// A north-south line through Sacramento, 2 km long, vertex every 100 m.
const LAT0 = 38.5;
const LNG0 = -121.5;
const line: [number, number][] = Array.from({ length: 21 }, (_, i) => [LAT0 + (i * 100) / 111320, LNG0]);
const cum = cumulativeMeters(line);
/** Metres east of the line at the given latitude. */
const east = (m: number, lat = LAT0 + 1000 / 111320) => ({ lat, lng: LNG0 + m / (111320 * Math.cos((lat * Math.PI) / 180)) });

describe("compassOf / bearingDeg", () => {
  test("eight points, boundaries at 22.5 degrees", () => {
    expect(compassOf(0)).toBe("north");
    expect(compassOf(22)).toBe("north");
    expect(compassOf(23)).toBe("northeast");
    expect(compassOf(90)).toBe("east");
    expect(compassOf(180)).toBe("south");
    expect(compassOf(270)).toBe("west");
    expect(compassOf(337)).toBe("northwest");
    expect(compassOf(338)).toBe("north");
    expect(compassOf(-90)).toBe("west");
  });

  test("bearing from the driver to a point due east is 90, due north is 0", () => {
    const a = { lat: LAT0, lng: LNG0 };
    expect(bearingDeg(a, east(500, LAT0))).toBeCloseTo(90, 0);
    expect(bearingDeg(a, { lat: LAT0 + 0.01, lng: LNG0 })).toBeCloseTo(0, 5);
  });
});

describe("spokenMiles", () => {
  test("feet under a tenth of a mile, one decimal otherwise, singular mile", () => {
    expect(spokenMiles(100)).toBe("350 feet");
    expect(spokenMiles(500)).toBe("0.3 miles");
    expect(spokenMiles(1609.34)).toBe("1 mile");
    expect(spokenMiles(2500)).toBe("1.6 miles");
    expect(spokenMiles(20000)).toBe("12 miles");
  });
});

describe("backOnRoute", () => {
  test("driver 500 m east of a north-south line: 0.3 miles to the west", () => {
    const r = backOnRoute(line, cum, 10, east(500), 0)!;
    expect(r.distM).toBeCloseTo(500, -1);
    expect(r.bearingDeg).toBeCloseTo(270, 0);
    expect(r.compass).toBe("west");
    expect(r.relative).toBe("to your left"); // heading north, the line is on the left
    expect(r.text).toBe("Lost signal, can't reroute. The route is 0.3 miles to the west.");
    expect(r.line).toBe("Return to the route: 0.3 mi W");
  });

  test("works without a usable match index or heading, and past the end of the line", () => {
    const r = backOnRoute(line, cum, -1, { lat: LAT0 + 3000 / 111320, lng: LNG0 }, null)!;
    expect(r.compass).toBe("south");
    expect(r.distM).toBeCloseTo(1000, -1);
    expect(r.relative).toBeNull();
    expect(r.text).toBe("Lost signal, can't reroute. The route is 0.6 miles to the south.");
  });

  test("heading: ahead / behind / right", () => {
    expect(backOnRoute(line, cum, 10, east(500), 270)!.relative).toBe("ahead");
    expect(backOnRoute(line, cum, 10, east(500), 90)!.relative).toBe("behind");
    expect(backOnRoute(line, cum, 10, east(500), 180)!.relative).toBe("to your right");
  });

  test("no geometry: null", () => {
    expect(backOnRoute([], [0], 0, east(500))).toBeNull();
  });
});

describe("createNetAnnouncer", () => {
  test("offline must hold 5 s before 'lost'; online 5 s before 'back'; each once", () => {
    const a = createNetAnnouncer();
    expect(a.tick(true, T0)).toBeNull();
    expect(a.tick(false, T0 + 1000)).toBeNull(); // just went down
    expect(a.tick(false, T0 + 1000 + NET_HOLD_MS - 1)).toBeNull();
    expect(a.tick(false, T0 + 1000 + NET_HOLD_MS)).toBe("lost");
    expect(a.tick(false, T0 + 20000)).toBeNull(); // said already
    expect(a.tick(true, T0 + 30000)).toBeNull();
    expect(a.tick(true, T0 + 30000 + NET_HOLD_MS)).toBe("back");
    expect(a.tick(true, T0 + 60000)).toBeNull();
  });

  test("a flap shorter than the hold says nothing", () => {
    const a = createNetAnnouncer();
    expect(a.tick(false, T0)).toBeNull();
    expect(a.tick(true, T0 + 2000)).toBeNull();
    expect(a.tick(true, T0 + 2000 + NET_HOLD_MS)).toBeNull(); // never lost, so never back
    expect(a.tick(false, T0 + 10000)).toBeNull();
    expect(a.tick(true, T0 + 12000)).toBeNull();
    expect(a.tick(false, T0 + 13000)).toBeNull();
    expect(a.tick(false, T0 + 13000 + NET_HOLD_MS)).toBe("lost"); // the hold restarts at each change
  });

  test("unknown is never announced; 'back' only after a 'lost'", () => {
    const a = createNetAnnouncer();
    expect(a.tick(null, T0)).toBeNull();
    expect(a.tick(null, T0 + 60000)).toBeNull();
    expect(a.tick(true, T0 + 61000)).toBeNull();
    expect(a.tick(true, T0 + 70000)).toBeNull();
  });

  test("reset forgets the loss", () => {
    const a = createNetAnnouncer();
    a.tick(false, T0);
    expect(a.tick(false, T0 + NET_HOLD_MS)).toBe("lost");
    a.reset();
    expect(a.tick(true, T0 + 20000)).toBeNull();
    expect(a.tick(true, T0 + 20000 + NET_HOLD_MS)).toBeNull();
  });
});
