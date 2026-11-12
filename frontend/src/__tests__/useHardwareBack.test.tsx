// Hardware Back on the map screen: only while the map is the screen on top.
import { renderHook } from "@testing-library/react-native";
import { BackHandler, Platform } from "react-native";

import { useHardwareBack, type BackLayers } from "../hooks/useHardwareBack";
import { useTripStore } from "../state/tripStore";
import type { ActiveRoute } from "../types";

const active = {
  place: { id: "p1", name: "Pilot", category: "truck_stop", lat: 38.5, lng: -121.5, tags: [] },
  route: { geometry: [[38.5, -121.5], [38.6, -121.5]], maneuvers: [], distance_m: 1000, distance_mi: 0.6, duration_s: 60, source: "here" },
} as unknown as ActiveRoute;

let press: () => boolean | null | undefined = () => false;

beforeEach(() => {
  Platform.OS = "android";
  useTripStore.getState().reset();
  jest.spyOn(BackHandler, "addEventListener").mockImplementation((_e, fn) => {
    press = fn as unknown as typeof press;
    return { remove: jest.fn() } as never;
  });
});
afterEach(() => jest.restoreAllMocks());

const layers = (focused: boolean, endRoute: () => void): BackLayers => ({
  focused,
  state: { micCard: false, routesOpen: false, reply: false, phase: "navigating" },
  cancel: jest.fn(),
  closeOptions: jest.fn(),
  endRoute,
});

test("route preview on top: Back ends the route", async () => {
  useTripStore.getState().startRoute(active);
  const endRoute = jest.fn();
  await renderHook(() => useHardwareBack(layers(true, endRoute), jest.fn(), jest.fn(), jest.fn()));
  expect(press()).toBe(true);
  expect(endRoute).toHaveBeenCalledTimes(1);
});

test("another screen on top (truck profile, history): Back is not ours, the route stays", async () => {
  useTripStore.getState().startRoute(active);
  const endRoute = jest.fn();
  await renderHook(() => useHardwareBack(layers(false, endRoute), jest.fn(), jest.fn(), jest.fn()));
  expect(press()).toBe(false); // the navigator closes the screen
  expect(endRoute).not.toHaveBeenCalled();
  // also while driving: "ignore" used to swallow the press and the screen could not be left with Back
  useTripStore.getState().setDriving(true);
  expect(press()).toBe(false);
});
