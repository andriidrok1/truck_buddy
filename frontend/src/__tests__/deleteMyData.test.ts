import AsyncStorage from "@react-native-async-storage/async-storage";

import { act, renderHook } from "@testing-library/react-native";

import { api } from "../api";
import { getToken, loadToken, setToken, tokenHold, useAuthState } from "../auth";
import { giveConsent, useConsent } from "../consent";
import { saveProfile } from "../data/profile";
import { loadEldToken, setEldToken } from "../eld";
import { saveArrived } from "../geo/arrival";
import { DEFAULT_TRUCK, getTruckProfile, isOnboarded, setOnboarded } from "../hooks/useTruckProfile";
import { loadLang, setLang, useLangStore } from "../i18n";
import { tripLog } from "../net/tripEvents";
import { deleteMyData, resetToFirstLaunch, rootScreen } from "../state/firstLaunch";
import { getAccount, saveAccount, setSignInNotice, signInNotice } from "../state/account";
import { loadHowTo, markHowToSeen } from "../state/howTo";
import { loadIntro, markIntroSeen } from "../state/intro";
import { saveActiveRoute } from "../state/routeStore";
import { setSpeedAlerts, useSettings } from "../state/settingsStore";
import { loadThemePref, setThemePref, useThemePref } from "../theme";
import { confirmHeight, isHeightConfirmed } from "../truck/heightConfirmed";
import { storage } from "../utils/storage";
import { __resetWipeGate, APP_KEYS, SECURE_KEYS, WIPE_PENDING_KEY } from "../utils/storage/pendingWipe";
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
  await saveAccount({ name: "Mike Johnson", email: "mike@example.com", provider: "google" });
  setThemePref("dark");
  await setOnboarded();
  await confirmHeight();
  await markHowToSeen();
  await markIntroSeen();
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
  expect(rootScreen(useLangStore.getState().chosen, await loadIntro(), consent.result.current, auth.result.current)).toBe("app");

  const router = { canDismiss: jest.fn(() => true), dismissAll: jest.fn() };
  const clearServer = jest.fn(async () => ({ deleted: 3 }));
  let ok = "";
  await act(async () => {
    ok = await deleteMyData(router, clearServer);
  });
  expect(ok).toBe("done");

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
  expect(await loadHowTo()).toBeNull(); // the next onboarding shows "How to use" again
  expect(await loadIntro()).toBe(false); // and the intro comes back after the language screen
  expect(getTruckProfile()).toEqual(DEFAULT_TRUCK);
  expect(useSettings.getState().speedAlerts).toBe(false);
  expect(tripLog.size()).toBe(0);
  expect(getAccount()).toBeNull();

  await consent.rerender({});
  await auth.rerender({});
  expect(consent.result.current).toBe("no");
  expect(auth.result.current).toBe("out");
  expect(rootScreen(useLangStore.getState().chosen, await loadIntro(), consent.result.current, auth.result.current)).toBe("language");
});

// The order of the gates has its own file: rootScreen.test.ts.

test("no server, nothing deleted: the phone keeps its data and the driver gets the error", async () => {
  await AsyncStorage.setItem("lang_v1", JSON.stringify("ru"));
  const router = { canDismiss: jest.fn(() => true), dismissAll: jest.fn() };
  const ok = await deleteMyData(router, async () => {
    throw new Error("offline");
  });
  expect(ok).toBe("failed");
  expect(router.dismissAll).not.toHaveBeenCalled();
  expect(await AsyncStorage.getItem("lang_v1")).toBe(JSON.stringify("ru"));
});

test("the router throws after the server wipe: the hold goes and the trip log sends again all the same (review of #173, L-1)", async () => {
  await setToken("tok", "driver", "code");
  const router = { canDismiss: () => true, dismissAll: jest.fn(() => {
    throw new Error("navigator gone");
  }) };
  const resume = jest.spyOn(tripLog, "resume");
  try {
    await expect(deleteMyData(router, async () => ({ deleted: 1 }))).rejects.toThrow("navigator gone");
    expect(tokenHold()).toBeNull();
    expect(resume).toHaveBeenCalled();
  } finally {
    resume.mockRestore();
  }
});

describe("the delete and the old token (review of #129)", () => {
  type Call = { method: string; path: string; auth: string | undefined };
  let calls: Call[];
  let finishDelete: (ok: boolean) => void;
  const realFetch = global.fetch;

  beforeEach(async () => {
    calls = [];
    global.fetch = jest.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
      const headers = (init?.headers ?? {}) as Record<string, string>;
      const call = { method: init?.method ?? "GET", path: String(url).replace("https://ci.invalid/api", ""), auth: headers.Authorization };
      calls.push(call);
      const body = (b: unknown) => new Response(JSON.stringify(b), { status: 200, headers: { "Content-Type": "application/json" } });
      if (call.method === "DELETE") {
        return new Promise<Response>((resolve, reject) => {
          finishDelete = (ok) => (ok ? resolve(body({ deleted: 1 })) : reject(new TypeError("Network request failed")));
        });
      }
      // The server: no token, no entry.
      if (!call.auth) return new Response(JSON.stringify({ detail: "Not authenticated" }), { status: 401, headers: { "Content-Type": "application/json" } });
      if (call.path.startsWith("/trips/events")) return body({ stored: 1 });
      return body({ turns: [] });
    }) as typeof fetch;
    await setToken("old-device-token", "driver");
  });
  afterEach(() => {
    global.fetch = realFetch;
  });

  const waitFor = async (cond: () => boolean) => {
    for (let i = 0; i < 50 && !cond(); i++) await flush();
    expect(cond()).toBe(true);
  };

  test("only the DELETE carries the old token once the delete has started; a send already on the wire lands before it", async () => {
    tripLog.log("question", { text: "before" });
    const early = tripLog.flush(); // on the wire before the driver confirmed
    const router = { canDismiss: () => false, dismissAll: jest.fn() };
    let done: Promise<string> = Promise.resolve("");
    await act(async () => {
      done = deleteMyData(router);
      await early;
      await waitFor(() => calls.some((c) => c.method === "DELETE"));
    });
    const deleteAt = calls.findIndex((c) => c.method === "DELETE");
    expect(calls[deleteAt].auth).toBe("Bearer old-device-token");
    expect(calls.slice(0, deleteAt).map((c) => c.path)).toEqual(["/trips/events"]);

    // While the DELETE is out: new events don't send, screen requests wait for it.
    tripLog.log("question", { text: "during" });
    expect(await tripLog.flush()).toBe("busy");
    const queued = api.memory().catch(() => undefined);
    await flush();
    expect(calls.length).toBe(deleteAt + 1);

    await act(async () => {
      finishDelete(true);
      expect(await done).toBe("done");
      await queued; // went out after the wipe, without a token
    });
    tripLog.log("question", { text: "after" });
    await tripLog.flush().catch(() => undefined);
    await api.memory().catch(() => undefined);

    const afterStart = calls.slice(deleteAt + 1);
    expect(afterStart.length).toBeGreaterThan(0);
    expect(afterStart.filter((c) => c.auth?.includes("old-device-token"))).toEqual([]);
  });

  test("the server can't be reached: checked, then the token and the trip log come back, nothing local is wiped, and it is unconfirmed (review of #173, L-4)", async () => {
    await AsyncStorage.setItem("lang_v1", JSON.stringify("ru"));
    const router = { canDismiss: () => true, dismissAll: jest.fn() };
    let done: Promise<string> = Promise.resolve("");
    let queued: Promise<unknown> = Promise.resolve();
    await act(async () => {
      done = deleteMyData(router);
      await waitFor(() => calls.some((c) => c.method === "DELETE"));
      // The map under the truck screen asks for something while the DELETE hangs: it waits, never goes tokenless.
      queued = api.memory();
      await flush();
      expect(calls.map((c) => c.method)).toEqual(["DELETE"]);
      finishDelete(false);
      // The server may have wiped: never "your data is still there". The check answers 200, which settles nothing.
      expect(await done).toBe("unconfirmed");
      await queued;
    });
    expect(calls[1]).toEqual({ method: "GET", path: "/auth/check", auth: "Bearer old-device-token" });
    // Same identity: the waiting request went with the old token, nothing signed the phone in anew.
    expect(calls[2]).toEqual({ method: "GET", path: "/memory", auth: "Bearer old-device-token" });
    expect(calls.some((c) => c.path === "/auth/refresh" || c.path === "/auth/renew")).toBe(false);
    expect(router.dismissAll).not.toHaveBeenCalled();
    expect(await AsyncStorage.getItem("lang_v1")).toBe(JSON.stringify("ru"));
    expect(getToken()).toBe("old-device-token");
    await api.memory();
    expect(calls[calls.length - 1].auth).toBe("Bearer old-device-token");
    tripLog.log("question", { text: "later" });
    expect(await tripLog.flush()).toBe("sent");
  });

  test("a 401 on a request that went out without the current token leaves the sign-in alone", async () => {
    await setToken(null);
    const late = api.memory().catch(() => undefined); // sent with no token: 401
    await setToken("new-device-token"); // the phone signed in meanwhile
    await late;
    expect(calls[calls.length - 1].auth).toBeUndefined();
    expect(getToken()).toBe("new-device-token");
  });
});

describe("Delete my data with an expired access token (re-review of #172)", () => {
  type Call = { method: string; path: string; auth: string | undefined };
  let calls: Call[];
  const realFetch = global.fetch;
  const reply = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
  const b64url = (v: string) => Buffer.from(v).toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  const EXPIRED = `${b64url('{"alg":"HS256"}')}.${b64url(JSON.stringify({ sub: "drv-1", exp: Math.floor(Date.now() / 1000) - 60 }))}.sig`;
  function server(refresh: "ok" | "refused") {
    calls = [];
    global.fetch = jest.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
      const headers = (init?.headers ?? {}) as Record<string, string>;
      const call = { method: init?.method ?? "GET", path: String(url).replace("https://ci.invalid/api", ""), auth: headers.Authorization };
      calls.push(call);
      if (call.path === "/auth/refresh") {
        return refresh === "ok" ? reply(200, { access_token: "FRESH", refresh_token: "R2", role: "driver" }) : reply(401, { detail: "Invalid or expired refresh token" });
      }
      if (call.path === "/auth/renew") return reply(401, { detail: "Invalid or expired token" });
      if (call.auth !== "Bearer FRESH") return reply(401, { detail: "Invalid or expired token" });
      if (call.method === "DELETE") return reply(200, { deleted: 2 });
      return reply(200, { turns: [] });
    }) as typeof fetch;
  }
  afterEach(() => {
    global.fetch = realFetch;
  });
  /** Fails the test instead of hanging jest when the delete never settles (the bug). */
  const within = <T,>(p: Promise<T>, ms = 3000) =>
    Promise.race([p, new Promise<never>((_, reject) => setTimeout(() => reject(new Error("deleteMyData never settled")), ms))]);

  test("the 401 lets the hold go, the token is renewed once, the DELETE goes again with the new token and the phone is wiped", async () => {
    await AsyncStorage.setItem("lang_v1", JSON.stringify("ru"));
    await setToken(EXPIRED, "driver", "code", "R1");
    server("ok");
    const router = { canDismiss: () => false, dismissAll: jest.fn() };
    let ok = "";
    await act(async () => {
      ok = await within(deleteMyData(router));
    });
    expect(ok).toBe("done");
    expect(calls.map((c) => `${c.method} ${c.path}`)).toEqual(["DELETE /memory", "POST /auth/refresh", "DELETE /memory"]);
    expect(calls[2].auth).toBe("Bearer FRESH");
    expect(getToken()).toBeNull();
    expect(await AsyncStorage.getItem("lang_v1")).toBeNull();
  });

  test("the renewal is refused: the server says the token is dead, so the phone signs out with the notice; nothing claims a deletion (review of #173, M-1)", async () => {
    await AsyncStorage.setItem("lang_v1", JSON.stringify("ru"));
    await setToken(EXPIRED, "driver", "code", "R1");
    server("refused");
    const router = { canDismiss: () => true, dismissAll: jest.fn() };
    let ok = "";
    await act(async () => {
      ok = await within(deleteMyData(router));
    });
    expect(ok).toBe("signed_out");
    expect(calls.filter((c) => c.method === "DELETE")).toHaveLength(1); // one retry only after a renewal that worked
    expect(router.dismissAll).not.toHaveBeenCalled();
    expect(getToken()).toBeNull();
    expect(signInNotice()).toBe("signed_out");
    expect(await AsyncStorage.getItem("lang_v1")).toBe(JSON.stringify("ru")); // a sign-out, not a fresh install
    await within(api.memory().catch(() => undefined)); // the hold is down: this does not wait forever
  });

  test("the renewal gets no answer: nothing is wiped, the phone stays signed in, the delete settles as failed", async () => {
    await AsyncStorage.setItem("lang_v1", JSON.stringify("ru"));
    await setToken(EXPIRED, "driver", "code", "R1");
    setSignInNotice(null);
    server("ok");
    const inner = global.fetch as jest.Mock;
    global.fetch = jest.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
      if (String(url).endsWith("/auth/refresh")) throw new TypeError("Network request failed");
      return inner(url, init);
    }) as typeof fetch;
    const router = { canDismiss: () => true, dismissAll: jest.fn() };
    let ok = "";
    await act(async () => {
      ok = await within(deleteMyData(router));
    });
    expect(ok).toBe("failed");
    expect(getToken()).toBe(EXPIRED);
    expect(signInNotice()).toBeNull();
    expect(await AsyncStorage.getItem("lang_v1")).toBe(JSON.stringify("ru"));
  });
});

describe("a partial wipe (review of #129)", () => {
  test("a key that will not go away keeps the pending flag, and the next cold start finishes the wipe before the first read", async () => {
    await AsyncStorage.setItem("lang_v1", JSON.stringify("ru"));
    await AsyncStorage.setItem("ai_consent_v2", "true");
    // removeItem is itself a jest.fn in the AsyncStorage mock: the other keys go through multiRemove.
    const spy = jest.spyOn(AsyncStorage, "removeItem").mockImplementation(async (k: string) => {
      if (k === "lang_v1") throw new Error("disk full");
      await AsyncStorage.multiRemove([k]);
    });
    let left: string[] = [];
    await act(async () => {
      left = await resetToFirstLaunch();
    });
    spy.mockRestore();
    jest.mocked(AsyncStorage.removeItem).mockImplementation((key: string) => AsyncStorage.multiRemove([key]));
    expect(left).toEqual(["lang_v1"]);
    expect(await AsyncStorage.getItem(WIPE_PENDING_KEY)).not.toBeNull();
    expect(await AsyncStorage.getItem("ai_consent_v2")).toBeNull();
    expect(useLangStore.getState().chosen).toBe(false); // memory already says first launch

    // Next cold start: the gate runs before any read, so the old language never comes back.
    __resetWipeGate();
    expect(await storage.getItem("lang_v1", null)).toBeNull();
    expect(await AsyncStorage.getItem(WIPE_PENDING_KEY)).toBeNull();
  });

  test("signed in again in the same run with the flag up: the old leftovers go, the new identity survives the next cold start", async () => {
    await AsyncStorage.setItem("lang_v1", JSON.stringify("ru"));
    await AsyncStorage.setItem("trip_arrived_v1", JSON.stringify({ trip: "old", ids: ["p1"] }));
    const stuck = new Set(["lang_v1", "trip_arrived_v1"]);
    const spy = jest.spyOn(AsyncStorage, "removeItem").mockImplementation(async (k: string) => {
      if (stuck.has(k)) throw new Error("disk full");
      await AsyncStorage.multiRemove([k]);
    });
    let left: string[] = [];
    await act(async () => {
      left = await resetToFirstLaunch();
    });
    expect(left.sort()).toEqual(["lang_v1", "trip_arrived_v1"]);
    expect(JSON.parse((await AsyncStorage.getItem(WIPE_PENDING_KEY)) ?? "null")).toEqual(expect.arrayContaining(["lang_v1", "trip_arrived_v1"]));
    stuck.clear(); // the disk recovers

    // The driver goes through the gates again: language, consent, a new device token.
    await act(async () => {
      setLang("en");
      await giveConsent();
      await setToken("new-device-token", "driver");
    });
    spy.mockRestore();
    jest.mocked(AsyncStorage.removeItem).mockImplementation((key: string) => AsyncStorage.multiRemove([key]));
    expect(await AsyncStorage.getItem(WIPE_PENDING_KEY)).toBeNull();
    expect(await AsyncStorage.getItem("trip_arrived_v1")).toBeNull(); // the old identity's leftover
    expect(await AsyncStorage.getItem("lang_v1")).toBe(JSON.stringify("en")); // the new choice

    // Next cold start: nothing of the new identity is wiped.
    __resetWipeGate();
    expect(await storage.getItem("lang_v1", null)).toBe("en");
    expect(await storage.getItem("ai_consent_v2", null)).toBe(true);
    expect(await storage.secureGet("auth_token", null)).toBe("new-device-token");
    await act(async () => {
      await resetToFirstLaunch(); // clean up for the next test (and the query cache's gc timer)
    });
  });

  test("the wipe list covers every key the app writes", async () => {
    const realFetch = global.fetch;
    // The profile push answers 500: it stays pending on disk (profile_unsynced_v1), without an offline mark
    // whose timer would outlive the test.
    global.fetch = jest.fn(async () => new Response("{}", { status: 500 })) as typeof fetch;
    await AsyncStorage.clear();
    mockSecure.clear();
    await fillTheAppUp();
    await setEldToken("motive-key");
    await new Promise((r) => setTimeout(r, 2100)); // the trip log's debounced mirror
    const listed = new Set<string>([...APP_KEYS, SCHEMA_KEY, WIPE_PENDING_KEY]);
    expect((await AsyncStorage.getAllKeys()).filter((k) => !listed.has(k))).toEqual([]);
    expect([...mockSecure.keys()].filter((k) => !(SECURE_KEYS as readonly string[]).includes(k))).toEqual([]);
    global.fetch = realFetch;
    await resetToFirstLaunch(); // clears the query cache, whose gc timer would keep jest alive
  });
});
