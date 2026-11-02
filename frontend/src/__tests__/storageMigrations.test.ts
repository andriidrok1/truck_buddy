import AsyncStorage from "@react-native-async-storage/async-storage";

import { ACTIVE_ROUTE_KEY, decodeSaved } from "../state/routeStore";
import { SETTINGS_KEY, parseSettings } from "../state/settingsStore";
import { CURRENT_SCHEMA, migrateStorage, SCHEMA_KEY, settingsFromV1 } from "../utils/storageMigrations";

const profile = { height_in: 158, trailer_ft: 53, weight_lb: 80000, hazmat: false, driver_name: "Ray" };
const saved = {
  active: { place: { id: "p1", name: "Pilot", category: "truck_stop", lat: 38.55, lng: -121.42, tags: [] }, route: { geometry: [[38.58, -121.49]], distance_m: 1, duration_s: 1, distance_mi: 0, duration_min: 0, summary: "", source: "here" } },
  savedAt: 1_700_000_000_000,
};

beforeEach(() => AsyncStorage.clear());

test("a fresh install only gets the version stamp", async () => {
  expect(await migrateStorage()).toBe(CURRENT_SCHEMA);
  expect(JSON.parse((await AsyncStorage.getItem(SCHEMA_KEY)) ?? "0")).toBe(CURRENT_SCHEMA);
  expect(await AsyncStorage.getItem(ACTIVE_ROUTE_KEY)).toBeNull();
  expect(parseSettings(JSON.parse((await AsyncStorage.getItem(SETTINGS_KEY)) ?? "null"))).toEqual({ handsFree: true, nudges: true, speedAlerts: false, voiceGuidance: true });
});

test("v1 -> v2: the route moves key and learns `driving`, the three profile encodings become one object, four booleans become one document", async () => {
  await AsyncStorage.setItem("active_route_v1", JSON.stringify(saved));
  await AsyncStorage.setItem("truck_profile_v1", JSON.stringify(JSON.stringify(profile))); // the double-encoded form
  await AsyncStorage.setItem("hands_free_v1", "false");
  await AsyncStorage.setItem("speed_alerts_v1", "true");
  // nudges and voice guidance never written: defaults
  expect(await migrateStorage()).toBe(2);
  const trip = decodeSaved(await AsyncStorage.getItem(ACTIVE_ROUTE_KEY));
  expect(trip?.active.place.id).toBe("p1");
  expect(trip?.driving).toBe(false);
  expect(await AsyncStorage.getItem("active_route_v1")).toBeNull();
  expect(JSON.parse((await AsyncStorage.getItem("truck_profile_v1")) ?? "null")).toEqual(profile);
  expect(JSON.parse((await AsyncStorage.getItem(SETTINGS_KEY)) ?? "null")).toEqual({ handsFree: false, nudges: true, speedAlerts: true, voiceGuidance: true });
  expect(await AsyncStorage.getItem("hands_free_v1")).toBeNull();
  // running again is a no-op
  await AsyncStorage.setItem("active_route_v1", JSON.stringify(saved));
  expect(await migrateStorage()).toBe(2);
  expect(await AsyncStorage.getItem("active_route_v1")).not.toBeNull();
});

test("the single- and plain-encoded profiles migrate too, a corrupt one is left alone", async () => {
  await AsyncStorage.setItem("truck_profile_v1", JSON.stringify(profile));
  await migrateStorage();
  expect(JSON.parse((await AsyncStorage.getItem("truck_profile_v1")) ?? "null")).toEqual(profile);
  await AsyncStorage.clear();
  await AsyncStorage.setItem("truck_profile_v1", JSON.stringify("{not json"));
  await migrateStorage();
  expect(await AsyncStorage.getItem("truck_profile_v1")).toBe(JSON.stringify("{not json"));
});

test("settingsFromV1 keeps defaults for missing or odd flags", () => {
  expect(settingsFromV1({ handsFree: false, nudges: "yes", speedAlerts: undefined })).toEqual({ handsFree: false, nudges: true, speedAlerts: false, voiceGuidance: true });
});
