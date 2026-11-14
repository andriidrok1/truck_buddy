// The start pins look around once, not on every GPS fix: one /places at a time, the answer reused near
// the same spot, a back-off after a failure, and a stale request aborted (audit 2026-11-13, item 1).
import { act, renderHook } from "@testing-library/react-native";

import { api } from "../api";
import { START_PINS_RETRY_MS, useStartPins } from "../hooks/useNearbyWarmup";
import { useTripStore } from "../state/tripStore";
import type { ActiveRoute, LatLng, Place } from "../types";

jest.mock("../api", () => ({ api: { places: jest.fn(), warm: jest.fn() } }));

const places = api.places as jest.Mock;
const stop = (id: string): Place => ({ id, name: id, category: "truck_stop", lat: 38.01, lng: -121.5, tags: [] });
const empty = { active: null, phase: "ready" as const, optionsCount: 0, providers: null, placesCount: 0 };
type Props = { c: LatLng; s?: Partial<typeof empty> & { active?: ActiveRoute | null } };

async function mount(c: LatLng, setFit = jest.fn()) {
  // Like the map screen: the pins on the map count as places, so pins shown end the look-around.
  return renderHook(
    ({ c, s }: Props) => {
      const placesCount = useTripStore((t) => t.places().length);
      useStartPins({ coords: c, isDemo: false, resolved: true }, { ...empty, placesCount, ...s }, { current: "ready" }, setFit);
    },
    { initialProps: { c } as Props },
  );
}

beforeEach(() => {
  places.mockReset();
  useTripStore.getState().reset();
  jest.useFakeTimers();
});
afterEach(() => jest.useRealTimers());

test("a moving truck with a slow /places sends one request, and its answer lands", async () => {
  places.mockImplementation(() => new Promise((r) => setTimeout(() => r({ places: [stop("a")] }), 2000)));
  const setFit = jest.fn();
  let lat = 38;
  const h = await mount({ lat, lng: -121.5 }, setFit);
  for (let i = 0; i < 30; i++) {
    lat += 0.00018; // a fix every 20 m
    await act(async () => {
      jest.advanceTimersByTime(1000);
    });
    await h.rerender({ c: { lat, lng: -121.5 } });
  }
  expect(places).toHaveBeenCalledTimes(1);
  expect(places.mock.calls[0][2]).toBeInstanceOf(AbortSignal);
  expect(useTripStore.getState().places()).toHaveLength(1);
  expect(setFit).toHaveBeenCalledTimes(1);
});

test("a failing /places backs off instead of retrying on every fix", async () => {
  places.mockRejectedValue(new Error("502"));
  let lat = 38;
  const h = await mount({ lat, lng: -121.5 });
  for (let i = 0; i < 20; i++) {
    lat += 0.00018;
    await act(async () => {
      jest.advanceTimersByTime(500);
    });
    await h.rerender({ c: { lat, lng: -121.5 } });
  }
  expect(places).toHaveBeenCalledTimes(1);
  // The back-off is over: the next fix tries again, then waits twice as long.
  await act(async () => {
    jest.advanceTimersByTime(START_PINS_RETRY_MS);
  });
  await h.rerender({ c: { lat: (lat += 0.00018), lng: -121.5 } });
  await act(async () => undefined);
  expect(places).toHaveBeenCalledTimes(2);
  await act(async () => {
    jest.advanceTimersByTime(START_PINS_RETRY_MS);
  });
  await h.rerender({ c: { lat: (lat += 0.00018), lng: -121.5 } });
  expect(places).toHaveBeenCalledTimes(2);
});

test("an empty answer is not asked again near the same spot, only ~5 km on", async () => {
  places.mockResolvedValue({ places: [] });
  const h = await mount({ lat: 38, lng: -121.5 });
  await act(async () => undefined);
  await h.rerender({ c: { lat: 38.01, lng: -121.5 } });
  await act(async () => undefined);
  expect(places).toHaveBeenCalledTimes(1);
  await h.rerender({ c: { lat: 38.06, lng: -121.5 } });
  await act(async () => undefined);
  expect(places).toHaveBeenCalledTimes(2);
});

test("pins come back from the last answer when the map empties again, without a request", async () => {
  places.mockResolvedValue({ places: [stop("a"), stop("b")] });
  const h = await mount({ lat: 38, lng: -121.5 });
  await act(async () => undefined);
  expect(useTripStore.getState().places()).toHaveLength(2);
  // A search takes the map, then is closed.
  useTripStore.getState().setStartPins([]);
  await h.rerender({ c: { lat: 38, lng: -121.5 }, s: { optionsCount: 3 } });
  await h.rerender({ c: { lat: 38.001, lng: -121.5 } });
  expect(places).toHaveBeenCalledTimes(1);
  expect(useTripStore.getState().places()).toHaveLength(2);
});

test("a request the map no longer wants is aborted", async () => {
  places.mockImplementation(() => new Promise(() => undefined));
  const h = await mount({ lat: 38, lng: -121.5 });
  const signal = places.mock.calls[0][2] as AbortSignal;
  expect(signal.aborted).toBe(false);
  await h.rerender({ c: { lat: 38, lng: -121.5 }, s: { optionsCount: 2 } });
  expect(signal.aborted).toBe(true);
});

test("unmount aborts the request in flight", async () => {
  places.mockImplementation(() => new Promise(() => undefined));
  const h = await mount({ lat: 38, lng: -121.5 });
  const signal = places.mock.calls[0][2] as AbortSignal;
  await h.unmount();
  expect(signal.aborted).toBe(true);
});
