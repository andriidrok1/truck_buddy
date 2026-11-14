import { renderHook } from "@testing-library/react-native";

import { cumulativeMeters } from "../geo/guidance";
import type { Guidance } from "../hooks/useGuidance";
import { useRouteAdvisories } from "../hooks/useRouteAdvisories";
import type { ActiveRoute } from "../types";

const DEG_PER_M = 1 / 111195;
// One 8 km segment (the backend simplifies a straight interstate to its two ends), then 1 km more.
const geometry: [number, number][] = [[0, 0], [0, 8000 * DEG_PER_M], [0, 9000 * DEG_PER_M]];
const cum = cumulativeMeters(geometry);
const active = {
  place: { id: "p1", name: "Harbor Truck Stop", category: "truck_stop", lat: 0, lng: 9000 * DEG_PER_M, tags: [] },
  route: {
    geometry,
    incidents: [{ type: "road_closure", closed: true, description: "I-80 closed", index: 1, criticality: "critical" }],
  },
} as unknown as ActiveRoute;

const guidance = (alongM: number) => ({ matchedIndex: 0, alongM, cum, limitMph: null }) as unknown as Guidance;

describe("useRouteAdvisories", () => {
  test("the chip counts down with the truck inside one long segment; the advisory is spoken once", async () => {
    const say = jest.fn();
    const h = await renderHook((g: Guidance) => useRouteAdvisories(active, g, 28, say), { initialProps: guidance(0) });
    expect(Math.round(h.result.current.advisories[0].aheadM)).toBe(8000);
    expect(say).toHaveBeenCalledTimes(1);
    expect(say.mock.calls[0][0]).toBe("Heads up: road closed in about 5 miles. I-80 closed.");

    await h.rerender(guidance(5000));
    expect(Math.round(h.result.current.advisories[0].aheadM)).toBe(3000);
    await h.rerender(guidance(7900));
    expect(Math.round(h.result.current.advisories[0].aheadM)).toBe(100);
    expect(say).toHaveBeenCalledTimes(1);
  });
});
