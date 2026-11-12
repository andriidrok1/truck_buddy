import AsyncStorage from "@react-native-async-storage/async-storage";

import { __resetSettings, ensureSettings, parseSettings, setMapView, SETTINGS_KEY, setHandsFree, setSpeedAlerts, useSettings, takeTruckReroute, requestTruckReroute } from "../state/settingsStore";

beforeEach(async () => {
  __resetSettings();
  await AsyncStorage.clear();
});

test("defaults, a set writes the one document", async () => {
  expect(useSettings.getState()).toMatchObject({ handsFree: true, nudges: true, speedAlerts: false, voiceGuidance: true });
  setSpeedAlerts(true);
  await new Promise((r) => setTimeout(r, 0));
  expect(JSON.parse((await AsyncStorage.getItem(SETTINGS_KEY)) ?? "null")).toEqual({ handsFree: true, nudges: true, speedAlerts: true, voiceGuidance: true, mapView: "map" });
});

test("ensureSettings reads the document; a set made while reading wins", async () => {
  await AsyncStorage.setItem(SETTINGS_KEY, JSON.stringify({ handsFree: false, nudges: false, speedAlerts: true, voiceGuidance: false }));
  const p = ensureSettings();
  setHandsFree(true); // the driver flipped it while the disk read was in flight
  const s = await p;
  expect(s).toEqual({ handsFree: true, nudges: false, speedAlerts: true, voiceGuidance: false, mapView: "map" });
  expect(useSettings.getState().loaded).toBe(true);
});

test("the truck editor's reroute flag is taken once", () => {
  expect(takeTruckReroute()).toBe(false);
  requestTruckReroute();
  expect(takeTruckReroute()).toBe(true);
  expect(takeTruckReroute()).toBe(false);
});

test("the map view is kept, and anything unknown on disk falls back to the plain map", async () => {
  setMapView("weather");
  expect(useSettings.getState().mapView).toBe("weather");
  expect(JSON.parse((await AsyncStorage.getItem(SETTINGS_KEY)) ?? "null").mapView).toBe("weather");
  expect(parseSettings({ mapView: "radar" }).mapView).toBe("map");
  expect(parseSettings({ mapView: "satellite" }).mapView).toBe("satellite");
});
