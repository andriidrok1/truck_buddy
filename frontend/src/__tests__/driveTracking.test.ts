/* eslint-disable @typescript-eslint/no-require-imports -- jest.resetModules needs a fresh require */
import { AppState } from "react-native";

jest.mock("expo", () => ({ isRunningInExpoGo: () => false }));
jest.mock("expo-task-manager", () => ({ defineTask: jest.fn() }));
jest.mock("expo-location", () => ({
  Accuracy: { BestForNavigation: 6 },
  ActivityType: { AutomotiveNavigation: 2 },
  hasStartedLocationUpdatesAsync: jest.fn(),
  startLocationUpdatesAsync: jest.fn(() => Promise.resolve()),
  stopLocationUpdatesAsync: jest.fn(() => Promise.resolve()),
}));

const L = require("expo-location");
const { startDriveTracking, stopDriveTracking } = require("../geo/driveTracking");

const titleOf = (i: number) => L.startLocationUpdatesAsync.mock.calls[i][1].foregroundService.notificationTitle;

beforeEach(async () => {
  L.hasStartedLocationUpdatesAsync.mockResolvedValue(false);
  await stopDriveTracking();
  jest.clearAllMocks();
  Object.defineProperty(AppState, "currentState", { value: "active", configurable: true });
});

test("a new destination while driving re-registers the task, so the notification names it", async () => {
  L.hasStartedLocationUpdatesAsync.mockResolvedValue(false);
  await startDriveTracking("Reno");
  L.hasStartedLocationUpdatesAsync.mockResolvedValue(true);
  await startDriveTracking("Reno"); // same trip: nothing to do
  expect(L.startLocationUpdatesAsync).toHaveBeenCalledTimes(1);
  await startDriveTracking("Sacramento");
  expect(L.startLocationUpdatesAsync).toHaveBeenCalledTimes(2);
  expect(titleOf(1)).toBe("Navigating to Sacramento");
});

test("in the background the running task is kept as it is (Android won't start a service from there)", async () => {
  L.hasStartedLocationUpdatesAsync.mockResolvedValue(false);
  await startDriveTracking("Reno");
  L.hasStartedLocationUpdatesAsync.mockResolvedValue(true);
  Object.defineProperty(AppState, "currentState", { value: "background", configurable: true });
  expect(await startDriveTracking("Sacramento")).toBe(true);
  expect(L.startLocationUpdatesAsync).toHaveBeenCalledTimes(1);
});
