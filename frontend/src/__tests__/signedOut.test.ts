// The phone changes hands without a restart: sign-out, or the server turning the token down. What goes, what
// stays, and that nothing of the driver who left reaches the one who signs in next.
import AsyncStorage from "@react-native-async-storage/async-storage";
import { act, renderHook } from "@testing-library/react-native";

import { api, signInGoogle } from "../api";
import { getRefreshToken, getToken, loadToken, setToken, signedInVia, useAuthState } from "../auth";
import { giveConsent, useConsent } from "../consent";
import { fetchProfile, saveProfile, UNSYNCED_KEY } from "../data/profile";
import { eldHeader, loadEldToken, setEldToken } from "../eld";
import { saveArrived } from "../geo/arrival";
import { DEFAULT_TRUCK, getTruckProfile, isOnboarded, setOnboarded } from "../hooks/useTruckProfile";
import { loadLang, setLang, useLangStore } from "../i18n";
import { resetRenewalForTests } from "../net/tokenRenewal";
import { tripLog } from "../net/tripEvents";
import { queryClient } from "../query-client";
import { getAccount, saveAccount, setWelcomeBack, signInNotice, welcomeBackDue } from "../state/account";
import { finishSignedIn, prepareSignedIn } from "../state/afterSignIn";
import { countsAsSignedIn, effectiveAuth, resetToFirstLaunch, retireDeviceIdentity, rootScreen, signedOutReset } from "../state/firstLaunch";
import { loadHowTo, markHowToSeen } from "../state/howTo";
import { loadIntro, markIntroSeen } from "../state/intro";
import { loadActiveRoute, saveActiveRoute } from "../state/routeStore";
import { DEFAULT_SETTINGS, setSpeedAlerts, useSettings } from "../state/settingsStore";
import { useTripStore } from "../state/tripStore";
import { loadThemePref, setThemePref, useThemePref } from "../theme";
import { confirmHeight, isHeightConfirmed } from "../truck/heightConfirmed";
import { storage } from "../utils/storage";
import { __resetWipeGate, APP_KEYS, HELD_TRIP_KEY, IDENTITY_KEYS, KEPT_ON_SIGN_OUT, SECURE_KEYS, WIPE_PENDING_KEY } from "../utils/storage/pendingWipe";
import { SCHEMA_KEY } from "../utils/storageMigrations";

const mockSecure = new Map<string, string>();
jest.mock("expo-secure-store", () => ({
  getItemAsync: async (k: string) => mockSecure.get(k) ?? null,
  setItemAsync: async (k: string, v: string) => void mockSecure.set(k, v),
  deleteItemAsync: async (k: string) => void mockSecure.delete(k),
}));

const flush = () => new Promise((r) => setTimeout(r, 0));
const realFetch = global.fetch;
const PLACE = { id: "p1", name: "Alice's yard", category: "truck_stop", lat: 38.55, lng: -121.42, tags: [] };
const ROUTE = { geometry: [[38.58, -121.49]], distance_m: 1, duration_s: 1, distance_mi: 0, duration_min: 0, summary: "", source: "here" };

/** Everything a signed-in driver leaves on a phone. */
async function driveAs(name: string) {
  await setEldToken(`motive-key-of-${name}`);
  await setOnboarded();
  await confirmHeight();
  await markHowToSeen();
  await saveProfile({ ...DEFAULT_TRUCK, height_in: 168, driver_name: name, favorite_chains: ["Love's"] });
  setSpeedAlerts(true);
  await saveActiveRoute({ place: PLACE, route: ROUTE } as never);
  useTripStore.getState().startRoute({ place: PLACE, route: ROUTE } as never);
  useTripStore.getState().setProviders({ query: `${name}'s breakdown`, providers: [] } as never);
  await saveArrived("trip-1", new Set(["p1"]));
  tripLog.log("question", { text: `${name} asked where to park` });
  queryClient.setQueryData(["memory"], { turns: [{ role: "driver", text: `${name}'s question`, at: "2026-11-12T00:00:00Z" }] });
  await flush();
}

beforeEach(async () => {
  global.fetch = jest.fn(async () => new Response("{}", { status: 500 })) as typeof fetch; // nothing reaches a server unless a test says so
  await AsyncStorage.clear();
  mockSecure.clear();
  __resetWipeGate();
  resetRenewalForTests();
  await loadLang();
  await loadThemePref();
  await loadToken();
  await loadEldToken();
});
afterEach(async () => {
  global.fetch = jest.fn(async () => new Response("{}", { status: 500 })) as typeof fetch;
  await act(async () => {
    await resetToFirstLaunch(); // also clears the query cache, whose gc timer would keep jest alive
  });
  global.fetch = realFetch;
});

describe("signedOutReset", () => {
  async function phoneInUse() {
    await AsyncStorage.setItem(SCHEMA_KEY, "2");
    setLang("ru");
    await giveConsent();
    await markIntroSeen();
    setThemePref("dark");
    await setToken("ACC-A", "driver", "account", "REF-A");
    await saveAccount({ name: "Alice Smith", email: "alice@example.com", provider: "apple" });
    setWelcomeBack(true);
    await driveAs("Alice");
    await new Promise((r) => setTimeout(r, 2100)); // the trip log's debounced mirror on disk
  }

  test("the driver's data goes, in memory and on disk; the language, consent, the intro flag and the theme stay; the root shows sign-in", async () => {
    await phoneInUse();
    expect(await AsyncStorage.getItem(UNSYNCED_KEY)).not.toBeNull(); // a truck change the server never took
    expect(await AsyncStorage.getItem("trip_events_pending")).not.toBeNull();
    const consent = await renderHook(() => useConsent());
    const auth = await renderHook(() => useAuthState());
    await flush();
    expect(rootScreen(useLangStore.getState().chosen, await loadIntro(), consent.result.current, auth.result.current)).toBe("app");

    let left: string[] = ["?"];
    await act(async () => {
      left = await signedOutReset();
    });
    expect(left).toEqual([]);

    // Disk: only what is about the phone, plus the storage format stamp.
    expect([...(await AsyncStorage.getAllKeys())].sort()).toEqual([SCHEMA_KEY, ...KEPT_ON_SIGN_OUT].sort());
    expect(mockSecure.size).toBe(0); // token, refresh token, role, how it signed in, the Motive key
    expect(await AsyncStorage.getItem(WIPE_PENDING_KEY)).toBeNull();

    // Kept.
    expect(useLangStore.getState()).toMatchObject({ lang: "ru", chosen: true });
    expect(await loadIntro()).toBe(true);
    const theme = await renderHook(() => useThemePref());
    expect(theme.result.current).toBe("dark");
    await consent.rerender({});
    expect(consent.result.current).toBe("yes");

    // Gone.
    expect(getToken()).toBeNull();
    expect(getRefreshToken()).toBeNull();
    expect(signedInVia()).toBe("device"); // nothing remembered about how the last driver signed in
    expect(getAccount()).toBeNull();
    expect(welcomeBackDue()).toBe(false);
    expect(getTruckProfile()).toEqual(DEFAULT_TRUCK);
    expect(await isOnboarded()).toBe(false);
    expect(isHeightConfirmed()).toBe(false);
    expect(await loadHowTo()).toBeNull();
    expect(eldHeader()).toEqual({});
    expect(useSettings.getState()).toMatchObject(DEFAULT_SETTINGS);
    expect(useTripStore.getState()).toMatchObject({ active: null, providers: null, options: [], agentPlaces: [], startPins: [], driving: false });
    expect(await loadActiveRoute()).toBeNull();
    expect(tripLog.size()).toBe(0);
    expect(queryClient.getQueryData(["memory"])).toBeUndefined();
    expect(queryClient.getQueryCache().getAll()).toEqual([]);
    expect(signInNotice()).toBeNull(); // the driver asked for this: nothing to explain

    await auth.rerender({});
    expect(auth.result.current).toBe("out");
    expect(rootScreen(useLangStore.getState().chosen, await loadIntro(), consent.result.current, auth.result.current)).toBe("signin");
  });

  test("the lists agree: what stays plus what goes is every key the app writes", () => {
    const all = [...APP_KEYS, ...SECURE_KEYS];
    // The trip set aside for a driver the server signed out is neither: the sign-out pass leaves it, its own code removes it.
    expect([...IDENTITY_KEYS, ...KEPT_ON_SIGN_OUT, HELD_TRIP_KEY].sort()).toEqual([...all].sort());
    expect([...KEPT_ON_SIGN_OUT].sort()).toEqual(["ai_consent_v2", "intro_seen_v1", "lang_v1", "theme_pref_v1"]);
    for (const k of ["auth_token", "auth_refresh", "auth_refresh_sent", "auth_role", "auth_via", "account_v1", "profile_restore_v1", "apple_name_pending_v1", "eld_token_v1", "truck_profile_v1", "profile_unsynced_v1", "trip_events_pending", "trip_v2", "settings_v2", "onboarded_v1", "height_confirmed_v1", "how_to_seen_v1"]) {
      expect(IDENTITY_KEYS).toContain(k);
    }
  });

  test("with the notice, for a sign-out the driver did not ask for; two at once are one reset", async () => {
    await phoneInUse();
    let a: string[] = ["?"];
    let b: string[] = ["?"];
    await act(async () => {
      [a, b] = await Promise.all([signedOutReset({ notice: true }), signedOutReset({ notice: true })]);
    });
    expect(a).toBe(b);
    expect(signInNotice()).toBe("signed_out");
    expect(getToken()).toBeNull();
  });

  test("a key that would not go is finished on the next cold start, and that pass leaves the language and consent alone", async () => {
    await phoneInUse();
    const spy = jest.spyOn(AsyncStorage, "removeItem").mockImplementation(async (k: string) => {
      if (k === "truck_profile_v1") throw new Error("disk full");
      await AsyncStorage.multiRemove([k]);
    });
    let left: string[] = [];
    await act(async () => {
      left = await signedOutReset();
    });
    spy.mockRestore();
    jest.mocked(AsyncStorage.removeItem).mockImplementation((key: string) => AsyncStorage.multiRemove([key]));
    expect(left).toEqual(["truck_profile_v1"]);
    expect(JSON.parse((await AsyncStorage.getItem(WIPE_PENDING_KEY)) ?? "null")).toEqual(["truck_profile_v1"]);
    expect(getTruckProfile()).toEqual(DEFAULT_TRUCK); // memory already says nobody

    __resetWipeGate(); // a cold start
    expect(await storage.getItem("truck_profile_v1", null)).toBeNull();
    expect(await storage.getItem("lang_v1", null)).toBe("ru");
    expect(await storage.getItem("ai_consent_v2", null)).toBe(true);
    expect(await storage.getItem("intro_seen_v1", null)).toBe("seen");
    expect(await AsyncStorage.getItem(WIPE_PENDING_KEY)).toBeNull();
  });

  test("killed in the middle of the reset: the next cold start removes the driver's keys only", async () => {
    await phoneInUse();
    // What the reset writes first, then the process dies before any key is removed.
    await AsyncStorage.setItem(WIPE_PENDING_KEY, JSON.stringify(IDENTITY_KEYS));
    __resetWipeGate();
    expect(await storage.getItem("truck_profile_v1", null)).toBeNull();
    expect(await storage.secureGet("auth_token", null)).toBeNull();
    expect(await storage.getItem("lang_v1", null)).toBe("ru");
    expect(await storage.getItem("theme_pref_v1", null)).toBe("dark");
  });
});

describe("a phone that still holds the anonymous device token of build 1.0", () => {
  test("it does not count as signed in: the root shows the sign-in screen; an access code and an account do count", () => {
    expect(countsAsSignedIn("device")).toBe(false);
    expect(countsAsSignedIn("code")).toBe(true);
    expect(countsAsSignedIn("account")).toBe(true);
    expect(effectiveAuth("in", "device")).toBe("out");
    expect(effectiveAuth("in", "code")).toBe("in");
    expect(effectiveAuth("in", "account")).toBe("in");
    expect(effectiveAuth("loading", "device")).toBe("loading");
    expect(effectiveAuth("out", "device")).toBe("out");
    expect(rootScreen(true, true, "yes", effectiveAuth("in", "device"))).toBe("signin");
    expect(rootScreen(true, true, "yes", effectiveAuth("in", "code"))).toBe("app");
  });

  test("the device token and what the phone kept under it are cleared; the language and consent stay", async () => {
    setLang("ru");
    await giveConsent();
    await setToken("old-device-token", "driver", "device", "old-refresh");
    await driveAs("Dave");
    let did = false;
    await act(async () => {
      did = await retireDeviceIdentity();
    });
    expect(did).toBe(true);
    expect(getToken()).toBeNull();
    expect(mockSecure.size).toBe(0);
    expect(getTruckProfile()).toEqual(DEFAULT_TRUCK);
    expect(await isOnboarded()).toBe(false);
    expect(signInNotice()).toBeNull(); // nothing to explain: the sign-in screen is simply the next screen
    expect(useLangStore.getState()).toMatchObject({ lang: "ru", chosen: true });
    expect(await storage.getItem("ai_consent_v2", null)).toBe(true);
  });

  test("an access-code sign-in and an account are left alone", async () => {
    await setToken("tester-token", "tester", "code", "r");
    expect(await retireDeviceIdentity()).toBe(false);
    expect(getToken()).toBe("tester-token");
    await setToken("acc", "driver", "account", "r");
    expect(await retireDeviceIdentity()).toBe(false);
    expect(getToken()).toBe("acc");
    await setToken(null);
    expect(await retireDeviceIdentity()).toBe(false);
  });
});

describe("driver A leaves, driver B signs in on the same phone, same run", () => {
  const b64url = (v: string) => Buffer.from(v).toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  /** An access token as the server makes it: the account's id in `sub`, good for 30 days. */
  const jwt = (sub: string) => `${b64url('{"alg":"HS256"}')}.${b64url(JSON.stringify({ sub, sid: "s1", exp: Math.floor(Date.now() / 1000) + 30 * 86400 }))}.sig`;
  const ACC = { A: jwt("acct-alice"), B: jwt("acct-bob") };
  type Call = { method: string; path: string; auth: string | undefined; motive: string | undefined; body: string };
  let calls: Call[];
  let alive: Set<string>; // the access tokens the server still accepts
  let held: ((r: Response) => void) | null;
  const reply = (status: number, body: unknown = {}) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
  const account = (who: "A" | "B", over: Record<string, unknown> = {}) =>
    reply(200, { access_token: ACC[who], refresh_token: `REF-${who}`, token_type: "bearer", role: "driver", expires_in: 2592000, new_account: true, has_profile: false, name: who === "A" ? "Alice Smith" : "Bob Jones", email: null, ...over });

  function server() {
    calls = [];
    alive = new Set();
    held = null;
    global.fetch = jest.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
      const headers = (init?.headers ?? {}) as Record<string, string>;
      const call: Call = { method: init?.method ?? "GET", path: String(url).replace("https://ci.invalid/api", ""), auth: headers.Authorization, motive: headers["X-Motive-Key"], body: String(init?.body ?? "") };
      calls.push(call);
      if (call.path === "/auth/google") {
        const who = JSON.parse(call.body).id_token === "google-token-of-alice" ? "A" : "B";
        alive.add(`Bearer ${ACC[who]}`);
        return account(who);
      }
      if (call.path === "/auth/refresh" || call.path === "/auth/renew") return reply(401, { detail: "This device is no longer registered" });
      if (!call.auth || !alive.has(call.auth)) return reply(401, { detail: "This device is no longer registered" });
      if (call.path === "/speak") return new Promise<Response>((resolve) => (held = resolve)); // a slow one, answered by the test
      if (call.path === "/me/profile" && call.method === "GET") return reply(404, { detail: "No stored profile" });
      if (call.path === "/me/profile") return reply(503); // A's truck change never lands: it stays waiting on the phone
      if (call.path === "/trips/events") return reply(200, { stored: 1 });
      return reply(200, { turns: [] });
    }) as typeof fetch;
  }
  const waitFor = async (cond: () => boolean) => {
    for (let i = 0; i < 100 && !cond(); i++) await flush();
    expect(cond()).toBe(true);
  };

  test("nothing of A is sent under B's name or shown to B, and no anonymous device is registered in between", async () => {
    server();
    setLang("ru");
    await giveConsent();
    await markIntroSeen();

    // A signs in (a returning driver) and uses the app.
    await signInGoogle("google-token-of-alice", prepareSignedIn);
    expect(getToken()).toBe(ACC.A);
    await driveAs("Alice");
    await waitFor(() => calls.some((c) => c.method === "PUT" && c.path === "/me/profile"));
    expect(await AsyncStorage.getItem(UNSYNCED_KEY)).not.toBeNull(); // A's truck is still waiting to be sent
    const slow = api.speak("Alice's question, still on the wire").catch(() => "failed");
    await waitFor(() => held !== null);

    // The server closes A's session (the account was deleted from another phone). The next request finds out.
    alive.delete(`Bearer ${ACC.A}`);
    const before = calls.length;
    await act(async () => {
      await expect(api.memory()).rejects.toThrow("401");
    });
    expect(getToken()).toBeNull();
    expect(signInNotice()).toBe("signed_out");
    expect(calls.slice(before).map((c) => c.path)).toEqual(["/memory", "/auth/refresh"]); // the access token was just refused: only the refresh token is left to try
    expect(calls.some((c) => c.path === "/auth/device")).toBe(false);
    const consent = await renderHook(() => useConsent());
    const auth = await renderHook(() => useAuthState());
    await flush();
    expect(rootScreen(useLangStore.getState().chosen, await loadIntro(), consent.result.current, auth.result.current)).toBe("signin");

    // B signs in: a new account.
    const fromB = calls.length;
    await act(async () => {
      await finishSignedIn(await signInGoogle("google-token-of-bob", prepareSignedIn), "google");
    });
    expect(getToken()).toBe(ACC.B);
    expect(signInNotice()).toBeNull();

    // A's slow request comes back refused only now. It is not sent again with B's token.
    await act(async () => {
      held!(reply(401, { detail: "This device is no longer registered" }));
      expect(await slow).toBe("failed");
    });
    expect(getToken()).toBe(ACC.B); // and B is not signed out by A's 401

    // What the app does for whoever is signed in: pull the profile, send the trip log, ask for memory.
    await act(async () => {
      await fetchProfile();
      await tripLog.flush();
      tripLog.log("question", { text: "Bob asked for diesel" });
      await tripLog.flush();
      await api.memory();
      await api.agent("find diesel", { lat: 38.5, lng: -121.5 }).catch(() => undefined);
    });

    const afterB = calls.slice(fromB);
    expect(afterB.length).toBeGreaterThan(4);
    // Sent: nothing with A's token, and under B's token nothing A wrote.
    expect(afterB.filter((c) => c.auth?.includes(ACC.A))).toEqual([]);
    const underB = afterB.filter((c) => c.auth === `Bearer ${ACC.B}`);
    expect(underB.length).toBeGreaterThan(3);
    for (const c of underB) {
      expect(c.body).not.toMatch(/Alice/);
      expect(c.body).not.toMatch(/"height_in":168/);
      expect(c.motive).toBeUndefined();
    }
    expect(afterB.filter((c) => c.method === "PUT" && c.path === "/me/profile")).toEqual([]); // A's waiting truck was not pushed to B's account
    expect(underB.filter((c) => c.path === "/trips/events").map((c) => JSON.parse(c.body).map((e: { data: { text: string } }) => e.data.text))).toEqual([["Bob asked for diesel"]]);
    expect(afterB.some((c) => c.path === "/speak")).toBe(false);
    const agent = underB.find((c) => c.path === "/agent");
    expect(JSON.parse(agent!.body).truck_profile).toEqual(DEFAULT_TRUCK);

    // Shown: B starts from nothing.
    expect(getAccount()).toEqual({ name: "Bob Jones", email: null, provider: "google" });
    expect(getTruckProfile()).toEqual(DEFAULT_TRUCK);
    expect(await isOnboarded()).toBe(false); // B gets onboarding
    expect(isHeightConfirmed()).toBe(false);
    expect(await loadHowTo()).toBeNull();
    expect(welcomeBackDue()).toBe(false);
    expect(useSettings.getState().speedAlerts).toBe(false);
    expect(useTripStore.getState()).toMatchObject({ active: null, providers: null });
    expect(await loadActiveRoute()).toBeNull();
    expect(queryClient.getQueryData(["memory"])).toBeUndefined();
    expect(eldHeader()).toEqual({});
    for (const k of await AsyncStorage.getAllKeys()) expect(`${k}=${await AsyncStorage.getItem(k)}`).not.toMatch(/Alice/);
    for (const [k, v] of mockSecure) {
      expect(`${k}=${v}`).not.toMatch(/Alice|REF-A/i);
      expect(v).not.toContain(ACC.A);
    }
    // The phone's own choices survived the hand-over.
    expect(useLangStore.getState()).toMatchObject({ lang: "ru", chosen: true });
    await consent.rerender({});
    expect(consent.result.current).toBe("yes");
  });

  test("A's profile answer arriving after the hand-over does not become B's truck", async () => {
    server();
    await signInGoogle("google-token-of-alice", prepareSignedIn);
    let answerProfile: (r: Response) => void = () => {};
    const base = global.fetch;
    global.fetch = jest.fn((url: RequestInfo | URL, init?: RequestInit) => {
      if (String(url).endsWith("/me/profile") && (init?.method ?? "GET") === "GET" && (init?.headers as Record<string, string>).Authorization === `Bearer ${ACC.A}`) {
        return new Promise<Response>((resolve) => (answerProfile = resolve));
      }
      return base(url, init);
    }) as typeof fetch;
    const pull = fetchProfile().catch(() => undefined); // A's truck, on its way from the server
    await flush();
    await act(async () => {
      await signedOutReset();
      await signInGoogle("google-token-of-bob", prepareSignedIn);
    });
    await act(async () => {
      answerProfile(reply(200, { ...DEFAULT_TRUCK, height_in: 168, driver_name: "Alice" }));
      await pull;
    });
    expect(getTruckProfile()).toEqual(DEFAULT_TRUCK);
    expect(isHeightConfirmed()).toBe(false);
    expect(await AsyncStorage.getItem("truck_profile_v1")).toBeNull();
  });

  describe("the server signs a driver out in the middle of a drive", () => {
    const DRIVING_ROUTE = { place: PLACE, route: ROUTE, trip_id: "trip-alice-1" };
    async function aliceOnTheRoad() {
      server();
      await giveConsent();
      await signInGoogle("google-token-of-alice", prepareSignedIn);
      await driveAs("Alice");
      await saveActiveRoute(DRIVING_ROUTE as never, Date.now(), true);
      useTripStore.getState().setDriving(true);
      tripLog.log("turn_late", { text: "Alice missed exit 12" }, "trip-alice-1");
      await flush();
    }
    async function sessionClosed() {
      alive.delete(`Bearer ${ACC.A}`);
      await act(async () => {
        await expect(api.memory()).rejects.toThrow("401");
      });
      expect(getToken()).toBeNull();
    }

    test("the phone still goes to the sign-in screen with the driver's data cleared, but the route and the pending events are set aside", async () => {
      await aliceOnTheRoad();
      await sessionClosed();
      expect(signInNotice()).toBe("signed_out");
      expect(getTruckProfile()).toEqual(DEFAULT_TRUCK);
      expect(eldHeader()).toEqual({});
      expect(useTripStore.getState()).toMatchObject({ active: null, driving: false });
      expect(await AsyncStorage.getItem("trip_v2")).toBeNull(); // nobody who is not Alice can load it
      expect(JSON.parse((await AsyncStorage.getItem(HELD_TRIP_KEY)) ?? "null")).toMatchObject({ sub: "acct-alice" });
      // The events are on disk only: memory is empty, so the next mount reads them back once (not twice).
      expect(tripLog.size()).toBe(0);
      expect(JSON.parse((await AsyncStorage.getItem("trip_events_pending")) ?? "[]").map((e: { data: { text?: string } }) => e.data.text)).toContain("Alice missed exit 12");
      expect(await tripLog.flush()).not.toBe("sent"); // nobody to send them as
    });

    test("the same account signs in again: the route is back for the map to resume, and the events go out under its token", async () => {
      await aliceOnTheRoad();
      await sessionClosed();
      const from = calls.length;
      await act(async () => {
        await signInGoogle("google-token-of-alice", prepareSignedIn);
      });
      await tripLog.restore(); // the map mounting again (startTripLog): must not double what came back
      const saved = await loadActiveRoute();
      expect(saved?.active.place.name).toBe("Alice's yard");
      expect(saved?.driving).toBe(true);
      expect(await AsyncStorage.getItem(HELD_TRIP_KEY)).toBeNull();
      expect(await tripLog.flush()).toBe("sent");
      const sent = calls.slice(from).filter((c) => c.path === "/trips/events");
      expect(sent).toHaveLength(1);
      expect(sent[0].auth).toBe(`Bearer ${ACC.A}`);
      expect(sent[0].body).toMatch(/Alice missed exit 12/);
      const texts = JSON.parse(sent[0].body).map((e: { data: { text?: string } }) => e.data.text).filter(Boolean);
      expect(texts.filter((x: string) => x === "Alice missed exit 12")).toHaveLength(1); // once, not twice
      expect(sent[0].body).not.toMatch(/_who/); // the stamp stays on the phone
    });

    test("a different account signs in: the route and the events of the driver before are destroyed, nothing of them is sent", async () => {
      await aliceOnTheRoad();
      await sessionClosed();
      const from = calls.length;
      await act(async () => {
        await signInGoogle("google-token-of-bob", prepareSignedIn);
      });
      expect(await loadActiveRoute()).toBeNull();
      expect(await AsyncStorage.getItem(HELD_TRIP_KEY)).toBeNull();
      expect(await AsyncStorage.getItem("trip_v2")).toBeNull();
      expect(tripLog.size()).toBe(0);
      expect(await tripLog.flush()).toBe("empty");
      expect(calls.slice(from).filter((c) => c.path === "/trips/events")).toEqual([]);
      for (const k of await AsyncStorage.getAllKeys()) expect(`${k}=${await AsyncStorage.getItem(k)}`).not.toMatch(/Alice/);
    });

    test("not driving, or the driver's own Sign out: nothing is set aside", async () => {
      await aliceOnTheRoad();
      useTripStore.getState().setDriving(false); // a route on the map, the truck parked
      await sessionClosed();
      expect(await AsyncStorage.getItem(HELD_TRIP_KEY)).toBeNull();
      expect(tripLog.size()).toBe(0);

      await act(async () => {
        await signInGoogle("google-token-of-alice", prepareSignedIn);
      });
      await saveActiveRoute(DRIVING_ROUTE as never, Date.now(), true);
      useTripStore.getState().setDriving(true);
      await act(async () => {
        await signedOutReset(); // "Sign out" in the profile
      });
      expect(await AsyncStorage.getItem(HELD_TRIP_KEY)).toBeNull();
      expect(await AsyncStorage.getItem("trip_v2")).toBeNull();
    });
  });

  test("an event stamped for one driver is dropped, not sent, when another is signed in at send time", async () => {
    server();
    await signInGoogle("google-token-of-alice", prepareSignedIn);
    tripLog.log("question", { text: "Alice asked" });
    await setToken(ACC.B, "driver", "account", "REF-B"); // the identity changed under the log, whatever the path
    alive.add(`Bearer ${ACC.B}`);
    tripLog.log("question", { text: "Bob asked" });
    expect(await tripLog.flush()).toBe("sent");
    const sent = calls.filter((c) => c.path === "/trips/events");
    expect(sent).toHaveLength(1);
    expect(sent[0].body).toMatch(/Bob asked/);
    expect(sent[0].body).not.toMatch(/Alice/);
    expect(tripLog.size()).toBe(0);
  });

  test("an answer that arrives after the driver signed out is thrown away before any handler can write it", async () => {
    server();
    await signInGoogle("google-token-of-alice", prepareSignedIn);
    const slow = api.speak("still on the wire"); // held by the server
    await waitFor(() => held !== null);
    await act(async () => {
      await signedOutReset();
      await signInGoogle("google-token-of-bob", prepareSignedIn);
    });
    held!(reply(200, { audio_url: "/a.mp3" })); // a perfectly good answer, for the wrong driver
    await expect(slow).rejects.toThrow("signed out");
  });
});
