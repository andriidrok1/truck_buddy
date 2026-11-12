import { fireEvent, render, screen } from "@testing-library/react-native";

import { TimeSlider } from "../components/TimeSlider";
import type { RadarFrame } from "../map/mapView";
import type { Chip } from "../weather/routeWeather";
import { chipAt, layerAt, positionAt, sliderVisible, trackParts } from "../weather/timeSlider";

jest.mock("react-native-reanimated", () => {
  const { View } = jest.requireActual<typeof import("react-native")>("react-native");
  return {
    __esModule: true,
    default: { View },
    useSharedValue: (v: unknown) => {
      const sv = { value: v, get: () => sv.value, set: (n: unknown) => void (sv.value = n) };
      return sv;
    },
    useAnimatedStyle: (f: () => object) => f(),
    withSpring: (v: unknown) => v,
  };
});
jest.mock("../components/Icon", () => {
  const { Text } = jest.requireActual<typeof import("react-native")>("react-native");
  return { Icon: ({ name }: { name: string }) => <Text>{`icon:${name}`}</Text> };
});

const NOW = new Date("2026-11-12T00:30:00Z");
const FRAME: RadarFrame = { enabled: true, live: "n0q-202611120030", forecast_init: "2026-11-11T22:00:00Z", forecast_step_min: 15, forecast_max_min: 1080, tile_url: "/x/{layer}" };

function chip(over: Partial<Chip>): Chip {
  return { index: 0, lat: 39, lng: -120, eta: NOW.toISOString(), kind: "clear", severity: "info", ice_risk: false, reasons: [], warn: false, alert: null, ...over } as Chip;
}

describe("layerAt", () => {
  it("uses the live radar near now", () => {
    expect(layerAt(FRAME, new Date(NOW.getTime() + 5 * 60000), NOW)).toBe("n0q-202611120030");
  });
  it("counts HRRR steps from the model run, not from now", () => {
    // 1 h 50 min ahead = 02:20Z; the run started 22:00Z: 260 min, the closest 15-min step is 255 or 270.
    expect(layerAt(FRAME, new Date(NOW.getTime() + 110 * 60000), NOW)).toBe("hrrr-f255");
    expect(layerAt(FRAME, new Date(NOW.getTime() + 30 * 60000), NOW)).toBe("hrrr-f180");
  });
  it("past the forecast there is no picture", () => {
    expect(layerAt(FRAME, new Date(NOW.getTime() + 20 * 3600000), NOW)).toBeNull();
    expect(layerAt({ ...FRAME, forecast_init: null }, new Date(NOW.getTime() + 60 * 60000), NOW)).toBe("n0q-202611120030");
    expect(layerAt(null, NOW, NOW)).toBeNull();
  });
});

describe("positionAt", () => {
  const line: [number, number][] = [[38, -121], [39, -121], [39, -120]];
  it("walks along the route", () => {
    expect(positionAt(line, 0)).toEqual({ lat: 38, lng: -121 });
    expect(positionAt(line, 1)).toEqual({ lat: 39, lng: -120 });
    const mid = positionAt(line, 0.3)!;
    expect(mid.lng).toBe(-121);
    expect(mid.lat).toBeGreaterThan(38);
    expect(positionAt([], 0.5)).toBeNull();
  });
});

describe("track", () => {
  const list = [
    chip({ index: 0, eta: NOW.toISOString(), kind: "cloud" }),
    chip({ index: 3, eta: new Date(NOW.getTime() + 60 * 60000).toISOString(), kind: "rain" }),
    chip({ index: 5, eta: new Date(NOW.getTime() + 150 * 60000).toISOString(), kind: "snow", warn: true }),
  ];
  it("splits halfway between points, far hours paler", () => {
    const parts = trackParts(list, NOW, 172);
    expect(parts.map((p) => [p.from, p.to])).toEqual([[0, 30], [30, 105], [105, 172]]);
    expect(parts[2]).toMatchObject({ warn: true, rough: false });
    expect(trackParts(list, NOW, 400)[2].to).toBe(400);
  });
  it("the header shows the point closest in time", () => {
    expect(chipAt(list, new Date(NOW.getTime() + 140 * 60000))?.index).toBe(5);
    expect(chipAt([], NOW)).toBeNull();
  });
});

describe("TimeSlider", () => {
  it("shows the moment and the weather there; play steps forward", async () => {
    jest.useFakeTimers();
    const onChange = jest.fn();
    const c = chip({ index: 5, kind: "snow", temp_f: 27, place: "Truckee, CA" });
    await render(<TimeSlider totalMin={172} value={0} onChange={onChange} parts={[]} chip={c} now={NOW} />);
    expect(screen.getByText("Weather now")).toBeTruthy();
    expect(screen.getByText("27°")).toBeTruthy();
    await fireEvent.press(screen.getByTestId("time-slider-play"));
    jest.advanceTimersByTime(700);
    expect(onChange).toHaveBeenCalledWith(15);
    jest.useRealTimers();
  });

  it("is adjustable by accessibility actions in 15-minute steps", async () => {
    const onChange = jest.fn();
    await render(<TimeSlider totalMin={172} value={30} onChange={onChange} parts={[]} chip={null} now={NOW} />);
    fireEvent(screen.getByTestId("time-slider-track"), "accessibilityAction", { nativeEvent: { actionName: "increment" } });
    expect(onChange).toHaveBeenCalledWith(45);
  });
});

describe("sliderVisible", () => {
  const base = { mapView: "weather", hasRoute: true, driving: false, phase: "navigating" };
  it("only parked, in the weather view, with a route", () => {
    expect(sliderVisible(base)).toBe(true);
    expect(sliderVisible({ ...base, driving: true })).toBe(false); // FMCSA: voice only on the move
    expect(sliderVisible({ ...base, mapView: "map" })).toBe(false);
    expect(sliderVisible({ ...base, hasRoute: false })).toBe(false);
  });
});

describe("review fixes", () => {
  it("a forecast tile URL and key carry the HRRR run", async () => {
    const { radarOverlay } = jest.requireActual<typeof import("../map/mapView")>("../map/mapView");
    const o = radarOverlay(FRAME, "https://x", "hrrr-f255")!;
    expect(o.urlTemplate).toContain("&run=2026-11-11T22%3A00%3A00Z");
    expect(o.key).toBe("hrrr-f255@2026-11-11T22:00:00Z");
    expect(radarOverlay(FRAME, "https://x", null)!.key).toBe("n0q-202611120030"); // live: no run
  });
  it("shareAt: where the truck already is on the route", () => {
    const { shareAt } = jest.requireActual<typeof import("../weather/timeSlider")>("../weather/timeSlider");
    const line: [number, number][] = [[38, -121], [39, -121], [40, -121]];
    expect(shareAt(line, 0)).toBe(0);
    expect(shareAt(line, 1)).toBeCloseTo(0.5, 5);
    expect(shareAt(line, 2)).toBeCloseTo(1, 5);
  });
});
