// Fixes while driving with the screen off or another app in front (plan A3, 2026-11-02).
//
// The foreground watch (src/geo/location.ts) stops when the app leaves the screen, so guidance went quiet with
// the phone locked. While a route is driven, a location task runs instead: on Android as a foreground service
// with a "Navigating to …" notification, on iOS under UIBackgroundModes "location". Neither needs the
// background ("Always") permission: the service is started from the open app on Go, which keeps the
// while-in-use grant (expo-location LocationModule: "user-initiated foreground service ... does NOT require the
// background location permission"). Its fixes go into the same store the map and the guidance read.
//
// Expo Go has no task manager: there the app keeps the screen awake while driving and stays foreground-only.

import { isRunningInExpoGo } from "expo";
import * as Location from "expo-location";
import * as TaskManager from "expo-task-manager";
import { Platform } from "react-native";

import { publishFix } from "./location";

export const DRIVE_TASK = "truckbuddy-drive";

// Not Constants.executionEnvironment: Expo Go 58 reports "bare" like a dev build (checked on the emulator
// 2026-11-02); the ExpoGo native module exists only in Expo Go.
const supported = Platform.OS !== "web" && !isRunningInExpoGo();

if (supported) {
  // Must be defined at module scope, before the app registers anything (imported by the root layout).
  TaskManager.defineTask<{ locations?: Location.LocationObject[] }>(DRIVE_TASK, async ({ data, error }) => {
    if (error || !data?.locations?.length) return;
    for (const loc of data.locations) publishFix(loc);
  });
}


/** Starts the drive task; false when this build can't (Expo Go, web) or the OS refused (the caller keeps the
 *  screen awake instead). Safe to call again: one task. */
export async function startDriveTracking(destination: string): Promise<boolean> {
  if (!supported) return false;
  try {
    if (await Location.hasStartedLocationUpdatesAsync(DRIVE_TASK)) return true;
    await Location.startLocationUpdatesAsync(DRIVE_TASK, {
      accuracy: Location.Accuracy.BestForNavigation,
      timeInterval: 1000,
      distanceInterval: 5,
      activityType: Location.ActivityType.AutomotiveNavigation,
      pausesUpdatesAutomatically: false,
      showsBackgroundLocationIndicator: true,
      foregroundService: {
        notificationTitle: `Navigating to ${destination}`,
        notificationBody: "TruckBuddy keeps guiding you with the screen off.",
        killServiceOnDestroy: true,
      },
    });
    return true;
  } catch (e) {
    console.warn("[drive] background location unavailable", e);
    return false;
  }
}

export async function stopDriveTracking(): Promise<void> {
  if (!supported) return;
  try {
    if (await Location.hasStartedLocationUpdatesAsync(DRIVE_TASK)) await Location.stopLocationUpdatesAsync(DRIVE_TASK);
  } catch {
    // not running
  }
}
