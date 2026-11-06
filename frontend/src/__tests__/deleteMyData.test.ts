import AsyncStorage from "@react-native-async-storage/async-storage";

import { act, renderHook } from "@testing-library/react-native";

import { getToken, loadToken, setToken, useAuthState } from "../auth";
import { giveConsent, useConsent } from "../consent";
import { saveProfile } from "../data/profile";
import { loadEldToken, setEldToken } from "../eld";
import { saveArrived } from "../geo/arrival";
import { DEFAULT_TRUCK, getTruckProfile, isOnboarded, setOnboarded } from "../hooks/useTruckProfile";
import { loadLang, setLang, useLangStore } from "../i18n";
import { tripLog } from "../net/tripEvents";
import { deleteMyData, rootScreen } from "../state/firstLaunch";
import { saveActiveRoute } from "../state/routeStore";
import { setSpeedAlerts, useSettings } from "../state/settingsStore";
import { loadThemePref, setThemePref, useThemePref } from "../theme";
import { confirmHeight, isHeightConfirmed } from "../truck/heightConfirmed";
import { SCHEMA_KEY } from "../utils/storageMigrations";

// The Keychain native module is missing under jest; a map stands in for it (jest.mock is hoisted above the imports).
const mockSecure = new Map<string, string>();
jest.mock("expo-secure-store", () => ({
  getItemAsync: async (k: string) => mockSecure.get(k) ?? null,
  setItemAsync: async (k: string, v: string) => void mockSecure.set(k, v),
  deleteItemAsync: async (k: string) => void mockSecure.delete(k),
}));

const flush = () => new Promise((r) => setTimeout(r, 0));

async function fillTheAppUp() {
  await AsyncStorage.setItem(SCHEMA_KEY, "2");
  setLang("ru");
  await giveConsent();
  await setToken("old-device-token", "driver");
  await setEldToken("motive-key");
  setThemePref("dark");
  await setOnboarded();
  await confirmHeight();
  await saveProfile({ ...DEFAULT_TRUCK, height_in: 168, driver_name: "Mike" });
  setSpeedAlerts(true);
  const place = { id: "p1", name: "Pilot", category: "truck_stop", lat: 38.55, lng: -121.42, tags: [] };
  const route = { geometry: [[38.58, -121.49]], distance_m: 1, duration_s: 1, distance_mi: 0, duration_min: 0, summary: "", source: "here" };
  await saveActiveRoute({ place, route } as never);
  await saveArrived("trip-1", new Set(["p1"]));
  tripLog.log("question", { text: "park" });
  await flush();
}

test("Delete my data: storage holds no app key, every store is back to a fresh install, the root shows the language screen", async () => {
  await loadLang();
  await loadThemePref();
  await loadToken();
  await loadEldToken();
  await fillTheAppUp();
  expect((await AsyncStorage.getAllKeys()).length).toBeGreaterThan(5);
  expect(mockSecure.size).toBe(4); // token, role, via, the Motive key

  const consent = await renderHook(() => useConsent());
  const auth = await renderHook(() => useAuthState());
  await flush();
  expect(rootScreen(useLangStore.getState().chosen, consent.result.current, auth.result.current)).toBe("app");

  const router = { canDismiss: jest.fn(() => true), dismissAll: jest.fn() };
  const clearServer = jest.fn(async () => ({ deleted: 3 }));
  let ok = false;
  await act(async () => {
    ok = await deleteMyData(router, clearServer);
  });
  expect(ok).toBe(true);

  expect(clearServer).toHaveBeenCalledTimes(1);
  expect(router.dismissAll).toHaveBeenCalledTimes(1);
  // Only the storage format stamp is left, as a fresh install writes it on its first start.
  expect(await AsyncStorage.getAllKeys()).toEqual([SCHEMA_KEY]);
  expect(mockSecure.size).toBe(0);
  expect(getToken()).toBeNull();

  expect(useLangStore.getState().chosen).toBe(false);
  expect(useLangStore.getState().lang).toBe("en"); // the phone's language under jest
  const theme = await renderHook(() => useThemePref());
  expect(theme.result.current).toBe("light");
  expect(isHeightConfirmed()).toBe(false);
  expect(await isOnboarded()).toBe(false);
  expect(getTruckProfile()).toEqual(DEFAULT_TRUCK);
  expect(useSettings.getState().speedAlerts).toBe(false);
  expect(tripLog.size()).toBe(0);

  await consent.rerender({});
  await auth.rerender({});
  expect(consent.result.current).toBe("no");
  expect(auth.result.current).toBe("out");
  expect(rootScreen(useLangStore.getState().chosen, consent.result.current, auth.result.current)).toBe("language");
});

test("the gates come in first-launch order: language, consent, a new anonymous sign-in, then the app", () => {
  expect(rootScreen(false, "no", "out")).toBe("language");
  expect(rootScreen(true, "no", "out")).toBe("consent");
  expect(rootScreen(true, "yes", "out")).toBe("device");
  expect(rootScreen(true, "yes", "in")).toBe("app");
});

test("no server, nothing deleted: the phone keeps its data and the driver gets the error", async () => {
  await AsyncStorage.setItem("lang_v1", JSON.stringify("ru"));
  const router = { canDismiss: jest.fn(() => true), dismissAll: jest.fn() };
  const ok = await deleteMyData(router, async () => {
    throw new Error("offline");
  });
  expect(ok).toBe(false);
  expect(router.dismissAll).not.toHaveBeenCalled();
  expect(await AsyncStorage.getItem("lang_v1")).toBe(JSON.stringify("ru"));
});
