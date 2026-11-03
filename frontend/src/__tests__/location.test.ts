import * as Location from "expo-location";

import { enableLocation, getLocation, publishFix, resetLocationForTests, startLocation, subscribeLocation } from "../geo/location";

jest.mock("expo-location", () => ({
  Accuracy: { High: 4, Balanced: 3 },
  getForegroundPermissionsAsync: jest.fn(),
  requestForegroundPermissionsAsync: jest.fn(),
  getLastKnownPositionAsync: jest.fn(),
  watchPositionAsync: jest.fn(),
  getCurrentPositionAsync: jest.fn(),
}));

const L = Location as jest.Mocked<typeof Location>;
const fix = (lat: number, lng: number, timestamp = 1700000000000) =>
  ({ coords: { latitude: lat, longitude: lng, heading: 90, speed: 20, accuracy: 5 }, timestamp }) as Location.LocationObject;

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
  onFix(fix(38.0, -121.0, 1700000001000));
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


test("ask: false never shows the system prompt (the consent screen just did)", async () => {
  L.getForegroundPermissionsAsync.mockResolvedValue({ status: "denied", canAskAgain: true } as never);
  await startLocation({ ask: false });
  expect(L.requestForegroundPermissionsAsync).not.toHaveBeenCalled();
  expect(getLocation()).toMatchObject({ isDemo: true, resolved: true });
  // the map screen (default ask) prompts, and a grant starts the watch
  L.requestForegroundPermissionsAsync.mockResolvedValue({ status: "granted", canAskAgain: true } as never);
  await startLocation();
  expect(L.requestForegroundPermissionsAsync).toHaveBeenCalledTimes(1);
  expect(L.watchPositionAsync).toHaveBeenCalledTimes(1);
});

test("the first fix timing out keeps the watch and marks the demo point resolved", async () => {
  L.getCurrentPositionAsync.mockRejectedValue(new Error("no fix"));
  await startLocation();
  expect(L.watchPositionAsync).toHaveBeenCalledTimes(1);
  expect(getLocation()).toMatchObject({ isDemo: true, resolved: true });
  const onFix = L.watchPositionAsync.mock.calls[0][1] as (l: Location.LocationObject) => void;
  onFix(fix(38.0, -121.0));
  expect(getLocation()).toMatchObject({ isDemo: false, coords: { lat: 38.0, lng: -121.0 } });
});

test("the watch and the drive task feed one store; an older or repeated fix is dropped", () => {
  resetLocationForTests();
  const fix = (ts: number, lat: number) => ({ coords: { latitude: lat, longitude: -121.5, heading: 0, speed: 20, accuracy: 5, altitude: 0, altitudeAccuracy: 0 }, timestamp: ts }) as never;
  publishFix(fix(1000, 38.5));
  publishFix(fix(2000, 38.6));
  publishFix(fix(2000, 38.7)); // the same fix from the other source
  publishFix(fix(1500, 38.8)); // late
  expect(getLocation().coords.lat).toBe(38.6);
  expect(getLocation().ts).toBe(2000);
});

test("a fix dated in the future does not freeze the store", () => {
  resetLocationForTests();
  const now = 1_700_000_000_000;
  const at = (ts: number, lat: number) => ({ coords: { latitude: lat, longitude: -121.5, heading: 0, speed: 20, accuracy: 5, altitude: 0, altitudeAccuracy: 0 }, timestamp: ts }) as never;
  publishFix(at(now + 3_600_000, 38.5), now); // one bogus fix an hour ahead
  publishFix(at(now + 1000, 38.6), now + 1000); // the next real one
  expect(getLocation().coords.lat).toBe(38.6);
  publishFix(at(now + 1000, 38.7), now + 1000); // and the guard is back: the same fix again is dropped
  expect(getLocation().coords.lat).toBe(38.6);
});
