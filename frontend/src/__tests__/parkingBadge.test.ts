import { parkingBadgeText, showsParkingBadge, STALE_AFTER_MS } from "../geo/parkingBadge";
import type { Place } from "../types";

const base: Place = { id: "p1", name: "Flying J", category: "truck_stop", lat: 0, lng: 0, tags: [] };
const NOW = Date.UTC(2026, 9, 27, 19, 50, 0);
const iso = (msAgo: number) => new Date(NOW - msAgo).toISOString();

describe("parkingBadgeText", () => {
  test("count + capacity + trend", () => {
    const b = parkingBadgeText({ ...base, parking_available: 56, parking_capacity: 68, parking_trend: "CLEARING", parking_updated: iso(60_000) }, NOW);
    expect(b).toEqual({ text: "56/68 open · clearing", tone: "live", trend: "CLEARING", asOf: null });
  });

  test("low: flagged almost full", () => {
    expect(parkingBadgeText({ ...base, parking_low: true }, NOW)).toMatchObject({ text: "almost full", tone: "low" });
    expect(parkingBadgeText({ ...base, parking_available: 3, parking_capacity: 68, parking_low: true, parking_trend: "FILLING" }, NOW)).toMatchObject({
      text: "3/68 open · almost full",
      tone: "low",
      trend: "FILLING",
    });
  });

  test("capacity only", () => {
    expect(parkingBadgeText({ ...base, parking_capacity: 68 }, NOW)).toMatchObject({ text: "68 spots", tone: "live", trend: null });
  });

  test("stale timestamp goes grey with a clock time", () => {
    const updated = iso(STALE_AFTER_MS + 60_000);
    const b = parkingBadgeText({ ...base, parking_available: 40, parking_capacity: 68, parking_updated: updated }, NOW);
    const expected = new Date(updated).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }).toLowerCase();
    expect(b).toMatchObject({ text: "40/68 open", tone: "stale", asOf: `as of ${expected}` });
    // Exactly at the threshold it is still live.
    expect(parkingBadgeText({ ...base, parking_available: 40, parking_updated: iso(STALE_AFTER_MS) }, NOW)?.tone).toBe("live");
  });

  test("garbage timestamp is ignored", () => {
    expect(parkingBadgeText({ ...base, parking_available: 40, parking_updated: "not a date" }, NOW)).toMatchObject({ tone: "live", asOf: null });
  });

  test("none of the fields: null", () => {
    expect(parkingBadgeText(base, NOW)).toBeNull();
    expect(parkingBadgeText({ ...base, parking_available: null, parking_capacity: null, parking_low: null, parking_trend: null }, NOW)).toBeNull();
  });
});

describe("showsParkingBadge", () => {
  test("only truck stops and rest areas with any parking_* field", () => {
    expect(showsParkingBadge({ ...base, parking_capacity: 10 })).toBe(true);
    expect(showsParkingBadge({ ...base, category: "rest_area", parking_trend: "STEADY" })).toBe(true);
    expect(showsParkingBadge({ ...base, category: "hotel", parking_capacity: 10 })).toBe(false);
    expect(showsParkingBadge(base)).toBe(false);
  });
});
