import { fireEvent, render, screen } from "@testing-library/react-native";
import { View } from "react-native";

import { frameClock, nativeMapType, radarOverlay, type RadarFrame } from "../map/mapView";
import { mapViewOptions, MapViewSheet } from "../components/MapViewSheet";

jest.mock("react-native-reanimated", () => {
  const { View: RNView } = jest.requireActual<typeof import("react-native")>("react-native");
  return {
    __esModule: true,
    default: { View: RNView },
    useSharedValue: (v: unknown) => {
      const sv = { value: v, get: () => sv.value, set: (n: unknown) => void (sv.value = n) };
      return sv;
    },
    useAnimatedStyle: (f: () => object) => f(),
    withSpring: (v: unknown) => v,
  };
});
jest.mock("react-native-safe-area-context", () => {
  const { View: RNView } = jest.requireActual<typeof import("react-native")>("react-native");
  return {
    useSafeAreaInsets: () => ({ top: 47, bottom: 34, left: 0, right: 0 }),
    SafeAreaProvider: ({ children }: { children: React.ReactNode }) => <RNView>{children}</RNView>,
  };
});
jest.mock("../components/Icon", () => {
  const { Text } = jest.requireActual<typeof import("react-native")>("react-native");
  return { Icon: ({ name }: { name: string }) => <Text>{`icon:${name}`}</Text> };
});

const FRAME: RadarFrame = {
  enabled: true,
  time: "2026-11-12T00:10:00+00:00",
  live: "n0q-202611120010",
  forecast_init: "2026-11-11T22:00:00Z",
  forecast_step_min: 15,
  forecast_max_min: 1080,
  max_zoom: 10,
  tile_url: "/api/radar/tiles/{layer}/{z}/{x}/{y}.png?k=abc",
};

describe("map view helpers", () => {
  it("picks the native map type per look and platform", () => {
    expect(nativeMapType("map", "ios")).toBe("standard");
    expect(nativeMapType("satellite", "android")).toBe("satellite");
    expect(nativeMapType("weather", "ios")).toBe("mutedStandard");
    expect(nativeMapType("weather", "android")).toBe("standard"); // muted by WEATHER_MAP_STYLE there
  });

  it("builds an absolute radar tile template keyed by the frame", () => {
    const o = radarOverlay(FRAME, "https://api.truckbuddy.io", null);
    expect(o).toEqual({
      urlTemplate: "https://api.truckbuddy.io/api/radar/tiles/n0q-202611120010/{z}/{x}/{y}.png?k=abc",
      key: "n0q-202611120010",
      maxNativeZoom: 10,
    });
    expect(radarOverlay(FRAME, "https://x", "hrrr-f060")?.urlTemplate).toContain("/tiles/hrrr-f060/{z}/{x}/{y}.png");
  });

  it("draws no radar without a frame, with radar off, or before the first frame", () => {
    expect(radarOverlay(null, "https://x", null)).toBeNull();
    expect(radarOverlay({ enabled: false }, "https://x", null)).toBeNull();
    expect(radarOverlay({ ...FRAME, live: null }, "https://x", null)).toBeNull();
  });

  it("formats the frame time and ignores junk", () => {
    expect(frameClock(FRAME.time, "en-US")).toMatch(/\d{1,2}:10/);
    expect(frameClock(undefined)).toBeNull();
    expect(frameClock("not a date")).toBeNull();
  });

  it("offers satellite only where the map engine has imagery", () => {
    expect(mapViewOptions(true).map((o) => o.view)).toEqual(["map", "satellite", "weather"]);
    expect(mapViewOptions(false).map((o) => o.view)).toEqual(["map", "weather"]);
  });
});

describe("MapViewSheet", () => {
  it("marks the current view and reports a pick", async () => {
    const onPick = jest.fn();
    await render(<MapViewSheet visible value="weather" satellite onPick={onPick} onClose={jest.fn()} />);
    expect(screen.getByTestId("map-view-weather").props.accessibilityState).toEqual({ selected: true });
    await fireEvent.press(screen.getByTestId("map-view-satellite"));
    expect(onPick).toHaveBeenCalledWith("satellite");
  });

  it("keeps clear of the home indicator", async () => {
    await render(<MapViewSheet visible value="map" satellite={false} onPick={jest.fn()} onClose={jest.fn()} />);
    expect(screen.queryByTestId("map-view-satellite")).toBeNull();
    const pad = (screen.getByTestId("map-view-sheet").props.style as object[]).flat().reduce((a: number, s: any) => (s && typeof s.paddingBottom === "number" ? s.paddingBottom : a), 0);
    expect(pad).toBeGreaterThanOrEqual(34);
  });
});

void View;
