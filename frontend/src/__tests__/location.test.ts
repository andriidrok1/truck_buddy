import * as Location from "expo-location";

import { enableLocation, getLocation, resetLocationForTests, startLocation, subscribeLocation } from "../geo/location";

jest.mock("expo-location", () => ({
  Accuracy: { High: 4, Balanced: 3 },
  getForegroundPermissionsAsync: jest.fn(),
  requestForegroundPermissionsAsync: jest.fn(),
  getLastKnownPositionAsync: jest.fn(),
  watchPositionAsync: jest.fn(),
  getCurrentPositionAsync: jest.fn(),
}));

const L = Location as jest.Mocked<typeof Location>;
const fix = (lat: number, lng: number) =>
  ({ coords: { latitude: lat, longitude: lng, heading: 90, speed: 20, accuracy: 5 }, timestamp: 1700000000000 }) as Location.LocationObject;

beforeEach(() => {
  resetLocationForTests();
  jest.clearAllMocks();
  L.getForegroundPermissionsAsync.mockResolvedValue({ status: "granted", canAskAgain: true } as never);
  L.getLastKnownPositionAsync.mockResolvedValue(null);
  L.watchPositionAsync.mockResolvedValue({ remove: jest.fn() } as never);
  L.getCurrentPositionAsync.mockResolvedValue(fix(37.77, -122.42));
});

test("starts on the demo point and resolves to the first fix", async () => {
  expect(getLocation().isDemo).toBe(true);
  expect(getLocation().resolved).toBe(false);
  const seen = jest.fn();
  subscribeLocation(seen);
  await startLocation();
  expect(getLocation()).toMatchObject({ coords: { lat: 37.77, lng: -122.42 }, isDemo: false, resolved: true, speed: 20 });
  expect(seen).toHaveBeenCalled();
});

test("a second start is a no-op: one watch for the app", async () => {
  await startLocation();
  await startLocation();
  expect(L.watchPositionAsync).toHaveBeenCalledTimes(1);
  expect(L.getCurrentPositionAsync).toHaveBeenCalledTimes(1);
});

test("the watch feeds every later fix", async () => {
  await startLocation();
  const onFix = L.watchPositionAsync.mock.calls[0][1] as (l: Location.LocationObject) => void;
  onFix(fix(38.0, -121.0));
  expect(getLocation().coords).toEqual({ lat: 38.0, lng: -121.0 });
});

test("denied: the demo point stays, but resolved, so the map does not wait", async () => {
  L.getForegroundPermissionsAsync.mockResolvedValue({ status: "denied", canAskAgain: false } as never);
  await startLocation();
  expect(getLocation()).toMatchObject({ isDemo: true, resolved: true });
  expect(L.watchPositionAsync).not.toHaveBeenCalled();
  await enableLocation(); // no prompt left: Settings instead of asking again
  expect(L.requestForegroundPermissionsAsync).not.toHaveBeenCalled();
});

test("a last known position shows at once, the first fix is not awaited", async () => {
  L.getLastKnownPositionAsync.mockResolvedValue(fix(36.0, -120.0));
  await startLocation();
  expect(getLocation().coords).toEqual({ lat: 36.0, lng: -120.0 });
  expect(L.getCurrentPositionAsync).not.toHaveBeenCalled();
});
