// The account on the truck screen (APP-3): sign out, sign out of all phones, delete the account, and "Clear memory"
// per identity. The server is a fetch stand-in that answers like backend services/account_api.py.
import AsyncStorage from "@react-native-async-storage/async-storage";

import { ACCOUNT_DELETE_TIMEOUT_MS, api, WIPE_DATA_TIMEOUT_MS } from "../api";
import { getToken, holdToken, loadToken, setToken, tokenHold } from "../auth";
import { CONSENT_KEY, giveConsent } from "../consent";
import { saveProfile } from "../data/profile";
import { DEFAULT_TRUCK, getTruckProfile } from "../hooks/useTruckProfile";
import { loadLang, setLang, useLangStore } from "../i18n";
import { after401, resetRenewalForTests } from "../net/tokenRenewal";
import { queryClient } from "../query-client";
import { getAccount, saveAccount, setSignInNotice, signInNotice } from "../state/account";
import { clearConversations, deleteAccount, LOGOUT_WAIT_MS, signOut, signOutEverywhere, UNANSWERED_KEY } from "../state/accountActions";
import { deleteMyData, signedOutReset, wipeRunning } from "../state/firstLaunch";
import { markIntroSeen } from "../state/intro";
import { saveActiveRoute } from "../state/routeStore";
import { useTripStore } from "../state/tripStore";
import { storage } from "../utils/storage";
import { __resetWipeGate, HELD_TRIP_KEY } from "../utils/storage/pendingWipe";

const mockSecure = new Map<string, string>();
jest.mock("expo-secure-store", () => ({
  getItemAsync: async (k: string) => mockSecure.get(k) ?? null,
  setItemAsync: async (k: string, v: string) => void mockSecure.set(k, v),
  deleteItemAsync: async (k: string) => void mockSecure.delete(k),
}));

type Call = { path: string; method: string; auth: string | undefined };
let calls: Call[] = [];
type Answer = Response | "offline" | Promise<Response>;
let answers: Record<string, Answer> = {};
const realFetch = global.fetch;
const reply = (status: number, body: unknown = {}) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const key = (method: string, path: string) => `${method} ${path}`;

beforeAll(() => {
  global.fetch = jest.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    const path = String(url).replace("https://ci.invalid/api", "").split("?")[0];
    const method = init?.method ?? "GET";
    calls.push({ path, method, auth: (init?.headers as Record<string, string> | undefined)?.Authorization });
    const a = answers[key(method, path)] ?? reply(200, { ok: true });
    if (a === "offline") throw new TypeError("Network request failed");
    return (await a).clone();
  }) as typeof fetch;
});
afterAll(() => {
  queryClient.clear(); // saveProfile left the profile in the cache, with a garbage-collection timer of minutes
  global.fetch = realFetch;
});

const router = () => ({ canDismiss: jest.fn(() => true), dismissAll: jest.fn() });
const TRUCK = { ...DEFAULT_TRUCK, height_in: 168, driver_name: "Mike" };
const DELETED = { ok: true, deleted: 3, trip_events: 2, profile: true, sessions: 1, apple_revoked: null as boolean | null };

/** A driver signed in with Google, onboarded, on a Russian phone. */
async function signedIn(via: "account" | "code" | "device" = "account") {
  await AsyncStorage.clear();
  mockSecure.clear();
  __resetWipeGate();
  resetRenewalForTests();
  setSignInNotice(null);
  await loadLang();
  await loadToken();
  setLang("ru");
  await giveConsent();
  await markIntroSeen();
  await setToken("ACC", "driver", via, "REF");
  if (via === "account") await saveAccount({ name: "Mike Johnson", email: "mike@example.com", provider: "google" });
  answers = {};
  await saveProfile(TRUCK);
  await new Promise((r) => setTimeout(r, 0));
  calls = [];
}

const stillMike = async () => {
  expect(getToken()).toBe("ACC");
  expect(getTruckProfile().height_in).toBe(168);
  expect(getAccount()?.email).toBe("mike@example.com");
  expect(useLangStore.getState().chosen).toBe(true);
};

describe("Sign out", () => {
  test("online: this phone's session is closed, then the phone forgets the driver and the AI consent, and keeps the language", async () => {
    await signedIn();
    await signOut();
    expect(calls).toEqual([{ path: "/auth/logout", method: "POST", auth: "Bearer ACC" }]);
    expect(getToken()).toBeNull();
    expect(getTruckProfile()).toEqual(DEFAULT_TRUCK);
    expect(getAccount()).toBeNull();
    expect(useLangStore.getState().chosen).toBe(true);
    expect(await storage.getItem(CONSENT_KEY, false as boolean)).toBe(false); // review of #172: the next driver agrees for themselves
  });

  test("offline: the phone signs out all the same", async () => {
    await signedIn();
    answers[key("POST", "/auth/logout")] = "offline";
    await signOut();
    expect(calls.map((c) => c.path)).toEqual(["/auth/logout"]);
    expect(getToken()).toBeNull();
    expect(getAccount()).toBeNull();
  });

  test("no answer: after LOGOUT_WAIT_MS the phone signs out without waiting for the request", async () => {
    await signedIn();
    const close = jest.fn(() => new Promise<boolean>(() => {}));
    jest.useFakeTimers();
    try {
      const done = signOut(close);
      await jest.advanceTimersByTimeAsync(LOGOUT_WAIT_MS + 10);
      jest.useRealTimers();
      await done;
    } finally {
      jest.useRealTimers();
    }
    expect(close).toHaveBeenCalledTimes(1);
    expect(getToken()).toBeNull();
  });
});

describe("Sign out of all phones", () => {
  test("every session closes on the server, then this phone signs out", async () => {
    await signedIn();
    expect(await signOutEverywhere()).toBe("done");
    expect(calls).toEqual([{ path: "/auth/logout-all", method: "POST", auth: "Bearer ACC" }]);
    expect(getToken()).toBeNull();
    expect(getAccount()).toBeNull();
  });

  test("a 503 with our detail: still signed in, nothing cleared, no check", async () => {
    await signedIn();
    answers[key("POST", "/auth/logout-all")] = reply(503, { detail: "Sign-in is unavailable right now" });
    expect(await signOutEverywhere()).toBe("failed");
    expect(calls.map((c) => c.path)).toEqual(["/auth/logout-all"]);
    await stillMike();
  });

  describe("no answer: GET /api/auth/check with the same token (review of #173, M2)", () => {
    test.each([
      ["session_closed", { detail: "This device is no longer registered", code: "session_closed" }],
      ["no code, no longer registered (a server from before the codes)", { detail: "This device is no longer registered" }],
    ])("our 401 %s: the sessions are closed, this phone signs out as the driver asked (no notice)", async (_, body) => {
      await signedIn();
      answers[key("POST", "/auth/logout-all")] = "offline";
      answers[key("GET", "/auth/check")] = reply(401, body);
      expect(await signOutEverywhere()).toBe("done");
      expect(calls.map((c) => c.path)).toEqual(["/auth/logout-all", "/auth/check"]);
      expect(calls[1].auth).toBe("Bearer ACC");
      expect(getToken()).toBeNull();
      expect(signInNotice()).toBeNull();
      expect(useLangStore.getState().chosen).toBe(true);
    });

    test("any other 401 without session_closed (an expired token): unconfirmed, still signed in (re-review, M-2)", async () => {
      await signedIn();
      answers[key("POST", "/auth/logout-all")] = "offline";
      answers[key("GET", "/auth/check")] = reply(401, { detail: "Invalid or expired token" });
      expect(await signOutEverywhere()).toBe("unconfirmed");
      await stillMike();
    });

    test("request() renewed the token before the call got no answer: the check carries the new token (re-review, M-2)", async () => {
      await signedIn();
      const call = jest.fn(async () => {
        await setToken("ACC2", "driver", "account", "REF2"); // what request() does on an expired token
        throw new TypeError("Network request failed");
      });
      answers[key("GET", "/auth/check")] = reply(200, { ok: true });
      expect(await signOutEverywhere(call)).toBe("failed");
      expect(calls).toEqual([{ path: "/auth/check", method: "GET", auth: "Bearer ACC2" }]);
      expect(getToken()).toBe("ACC2");
    });

    test("200: nothing closed, still signed in everywhere", async () => {
      await signedIn();
      answers[key("POST", "/auth/logout-all")] = reply(504, "gateway timeout");
      answers[key("GET", "/auth/check")] = reply(200, { ok: true });
      expect(await signOutEverywhere()).toBe("failed");
      await stillMike();
    });

    test("the check gets no answer either: unconfirmed, still signed in", async () => {
      await signedIn();
      answers[key("POST", "/auth/logout-all")] = "offline";
      answers[key("GET", "/auth/check")] = "offline";
      expect(await signOutEverywhere()).toBe("unconfirmed");
      await stillMike();
    });
  });
});

describe("Delete account and data", () => {
  test("200: DELETE /api/account, then a fresh install (the language screen)", async () => {
    await signedIn();
    answers[key("DELETE", "/account")] = reply(200, { ok: true, deleted: 3, trip_events: 2, profile: true, sessions: 1, apple_revoked: null });
    const r = router();
    const out = await deleteAccount(r);
    expect(out).toEqual({ ok: true, appleRevoked: null });
    expect(calls).toEqual([{ path: "/account", method: "DELETE", auth: "Bearer ACC" }]);
    expect(r.dismissAll).toHaveBeenCalled();
    expect(getToken()).toBeNull();
    expect(getAccount()).toBeNull();
    expect(getTruckProfile()).toEqual(DEFAULT_TRUCK);
    expect(useLangStore.getState().chosen).toBe(false);
    expect(await storage.getItem(CONSENT_KEY, false as boolean)).toBe(false);
  });

  test("Apple did not take the revocation: still a success, only logged", async () => {
    await signedIn();
    answers[key("DELETE", "/account")] = reply(200, { ok: true, apple_revoked: false });
    const warn = jest.spyOn(console, "warn").mockImplementation(() => {});
    expect(await deleteAccount(router())).toEqual({ ok: true, appleRevoked: false });
    expect(warn).toHaveBeenCalledWith(expect.stringContaining("Apple"));
    warn.mockRestore();
    expect(getToken()).toBeNull();
  });

  test.each([
    ["last_owner", "This account is the only owner of a fleet that still has drivers or a stored key. Write to support@truckbuddy.io to close the fleet or hand it over."],
    ["not_an_account", "This account can't be deleted from the app. Write to support@truckbuddy.io."],
  ])("409 %s: the message is picked by code, whatever the detail says, and nothing is cleared", async (code, message) => {
    await signedIn();
    answers[key("DELETE", "/account")] = reply(409, { detail: "some text the app does not read", code });
    const r = router();
    expect(await deleteAccount(r)).toEqual({ ok: false, reason: "refused", code, message });
    expect(r.dismissAll).not.toHaveBeenCalled();
    await stillMike();
  });

  test("409 without a code (an older server): the general refusal", async () => {
    await signedIn();
    answers[key("DELETE", "/account")] = reply(409, { detail: "This account is the only owner of a fleet." });
    expect(await deleteAccount(router())).toEqual({ ok: false, reason: "refused", code: null, message: "This account can't be deleted from the app. Write to support@truckbuddy.io." });
  });

  test("503 with our detail: nothing was removed, nothing cleared, no check", async () => {
    await signedIn();
    answers[key("DELETE", "/account")] = reply(503, { detail: "The account could not be deleted right now. Try again." });
    expect(await deleteAccount(router())).toEqual({ ok: false, reason: "not_removed" });
    expect(calls.map((c) => c.path)).toEqual(["/account"]);
    await stillMike();
  });

  test.each([
    ["500", () => reply(500, { detail: "Internal Server Error" })],
    ["429", () => reply(429, { detail: "Too many requests" })],
  ])("%s: failed, without claiming nothing was removed; nothing cleared", async (_, a) => {
    await signedIn();
    answers[key("DELETE", "/account")] = a();
    expect(await deleteAccount(router())).toEqual({ ok: false, reason: "failed" });
    expect(calls.map((c) => c.path)).toEqual(["/account"]);
    await stillMike();
  });

  describe("a 401 on the DELETE (review of #173, H1)", () => {
    test("account_gone: a deletion that already went through, the phone resets as on a 200, no renewal", async () => {
      await signedIn();
      answers[key("DELETE", "/account")] = reply(401, { detail: "This device is no longer registered", code: "account_gone" });
      const r = router();
      expect(await deleteAccount(r)).toEqual({ ok: true, appleRevoked: null });
      expect(calls.map((c) => `${c.method} ${c.path}`)).toEqual(["DELETE /account"]);
      expect(r.dismissAll).toHaveBeenCalled();
      expect(getToken()).toBeNull();
      expect(useLangStore.getState().chosen).toBe(false);
    });

    test("session_closed: the account is alive, so the forced sign-out with the notice, never a fresh install", async () => {
      await signedIn();
      answers[key("DELETE", "/account")] = reply(401, { detail: "This device is no longer registered", code: "session_closed" });
      const r = router();
      expect(await deleteAccount(r)).toEqual({ ok: false, reason: "signed_out" });
      expect(calls.map((c) => `${c.method} ${c.path}`)).toEqual(["DELETE /account"]);
      expect(r.dismissAll).not.toHaveBeenCalled();
      expect(getToken()).toBeNull();
      expect(signInNotice()).toBe("signed_out");
      expect(getAccount()).toBeNull();
      expect(useLangStore.getState().chosen).toBe(true);
    });

    test("no longer registered without a code (an older server), no earlier unanswered DELETE: the forced sign-out", async () => {
      await signedIn();
      answers[key("DELETE", "/account")] = reply(401, { detail: "This device is no longer registered" });
      expect(await deleteAccount(router())).toEqual({ ok: false, reason: "signed_out" });
      expect(signInNotice()).toBe("signed_out");
      expect(useLangStore.getState().chosen).toBe(true);
    });

    test("no longer registered without a code after this phone's DELETE got no answer (even across a restart): the deletion went through", async () => {
      await signedIn();
      answers[key("DELETE", "/account")] = "offline";
      answers[key("GET", "/auth/check")] = "offline";
      expect(await deleteAccount(router())).toEqual({ ok: false, reason: "unconfirmed" });
      expect(await storage.getItem(UNANSWERED_KEY, null as string | null)).not.toBeNull();
      answers[key("DELETE", "/account")] = reply(401, { detail: "This device is no longer registered" });
      expect(await deleteAccount(router())).toEqual({ ok: true, appleRevoked: null });
      expect(useLangStore.getState().chosen).toBe(false);
      expect(await storage.getItem(UNANSWERED_KEY, null as string | null)).toBeNull();
    });

    test("an answer after an unanswered DELETE takes the mark down: a later codeless 401 is a sign-out again", async () => {
      await signedIn();
      answers[key("DELETE", "/account")] = "offline";
      answers[key("GET", "/auth/check")] = "offline";
      await deleteAccount(router());
      answers[key("DELETE", "/account")] = reply(503, { detail: "The account could not be deleted right now. Try again." });
      expect(await deleteAccount(router())).toEqual({ ok: false, reason: "not_removed" });
      expect(await storage.getItem(UNANSWERED_KEY, null as string | null)).toBeNull();
      answers[key("DELETE", "/account")] = reply(401, { detail: "This device is no longer registered" });
      expect(await deleteAccount(router())).toEqual({ ok: false, reason: "signed_out" });
    });

    test("a sign-out takes the mark down with the driver's other keys", async () => {
      await signedIn();
      answers[key("DELETE", "/account")] = "offline";
      answers[key("GET", "/auth/check")] = "offline";
      await deleteAccount(router());
      await signedOutReset();
      expect(await AsyncStorage.getItem(UNANSWERED_KEY)).toBeNull();
    });

    test("an expired token: one renewal, one more DELETE with the new token, and no second round", async () => {
      await signedIn();
      answers[key("DELETE", "/account")] = reply(401, { detail: "Invalid or expired token" });
      answers[key("POST", "/auth/renew")] = reply(401, { detail: "Invalid or expired token" });
      answers[key("POST", "/auth/refresh")] = reply(200, { access_token: "ACC2", refresh_token: "REF2" });
      expect(await deleteAccount(router())).toEqual({ ok: false, reason: "unconfirmed" });
      const deletes = calls.filter((c) => c.method === "DELETE");
      expect(deletes.map((c) => c.auth)).toEqual(["Bearer ACC", "Bearer ACC2"]);
      expect(calls.filter((c) => c.path === "/auth/refresh")).toHaveLength(1);
      expect(getToken()).toBe("ACC2"); // still signed in: nothing on the phone was touched
      expect(getTruckProfile().height_in).toBe(168);
    });

    test("an expired token and a refused renewal: the phone signs out with the notice, never \"deleted\" (re-review, M-1)", async () => {
      await signedIn();
      answers[key("DELETE", "/account")] = reply(401, { detail: "Invalid or expired token" });
      answers[key("POST", "/auth/renew")] = reply(401, { detail: "Invalid or expired token" });
      answers[key("POST", "/auth/refresh")] = reply(401, { detail: "Invalid or expired refresh token" });
      const r = router();
      expect(await deleteAccount(r)).toEqual({ ok: false, reason: "signed_out" });
      expect(calls.filter((c) => c.method === "DELETE")).toHaveLength(1);
      expect(r.dismissAll).not.toHaveBeenCalled();
      expect(getToken()).toBeNull();
      expect(signInNotice()).toBe("signed_out");
      expect(useLangStore.getState().chosen).toBe(true); // a sign-out, not a fresh install
    });

    test("an expired token and a renewal with no answer: unconfirmed, still signed in", async () => {
      await signedIn();
      answers[key("DELETE", "/account")] = reply(401, { detail: "Invalid or expired token" });
      answers[key("POST", "/auth/renew")] = "offline";
      answers[key("POST", "/auth/refresh")] = "offline";
      expect(await deleteAccount(router())).toEqual({ ok: false, reason: "unconfirmed" });
      await stillMike();
      expect(signInNotice()).toBeNull();
    });

    test("an expired token, and the retry hears account_gone: the phone resets", async () => {
      await signedIn();
      let n = 0;
      const call = jest.fn(async () => {
        n++;
        if (n === 1) return api.deleteAccount();
        answers[key("DELETE", "/account")] = reply(401, { detail: "This device is no longer registered", code: "account_gone" });
        return api.deleteAccount();
      });
      answers[key("DELETE", "/account")] = reply(401, { detail: "Invalid or expired token" });
      answers[key("POST", "/auth/refresh")] = reply(200, { access_token: "ACC2", refresh_token: "REF2" });
      expect(await deleteAccount(router(), call)).toEqual({ ok: true, appleRevoked: null });
      expect(call).toHaveBeenCalledTimes(2);
      expect(getToken()).toBeNull();
    });
  });

  describe("no answer to the DELETE: GET /api/auth/check with the same token says what happened", () => {
    test("401 no longer registered without a code: this phone's DELETE just went unanswered, so the deletion went through", async () => {
      await signedIn();
      answers[key("DELETE", "/account")] = "offline";
      answers[key("GET", "/auth/check")] = reply(401, { detail: "This device is no longer registered" });
      const r = router();
      expect(await deleteAccount(r)).toEqual({ ok: true, appleRevoked: null });
      expect(calls).toEqual([
        { path: "/account", method: "DELETE", auth: "Bearer ACC" },
        { path: "/auth/check", method: "GET", auth: "Bearer ACC" },
      ]);
      expect(r.dismissAll).toHaveBeenCalled();
      expect(getToken()).toBeNull();
      expect(useLangStore.getState().chosen).toBe(false);
      expect(calls.map((c) => c.path)).not.toContain("/auth/refresh"); // no renewal, no "You were signed out"
    });

    test("account_gone from the check: the phone resets", async () => {
      await signedIn();
      answers[key("DELETE", "/account")] = "offline";
      answers[key("GET", "/auth/check")] = reply(401, { detail: "This device is no longer registered", code: "account_gone" });
      expect(await deleteAccount(router())).toEqual({ ok: true, appleRevoked: null });
      expect(useLangStore.getState().chosen).toBe(false);
    });

    test("session_closed from the check: the forced sign-out with the notice, never \"deleted\" (H-1)", async () => {
      await signedIn();
      answers[key("DELETE", "/account")] = "offline";
      answers[key("GET", "/auth/check")] = reply(401, { detail: "This device is no longer registered", code: "session_closed" });
      const r = router();
      expect(await deleteAccount(r)).toEqual({ ok: false, reason: "signed_out" });
      expect(r.dismissAll).not.toHaveBeenCalled();
      expect(getToken()).toBeNull();
      expect(signInNotice()).toBe("signed_out");
      expect(useLangStore.getState().chosen).toBe(true);
    });

    test("200: the account still answers, which may only mean the deletion is still running: unconfirmed (H2)", async () => {
      await signedIn();
      answers[key("DELETE", "/account")] = reply(504, "gateway timeout");
      answers[key("GET", "/auth/check")] = reply(200, { ok: true });
      expect(await deleteAccount(router())).toEqual({ ok: false, reason: "unconfirmed" });
      expect(calls.map((c) => c.path)).toEqual(["/account", "/auth/check"]);
      await stillMike();
    });

    test("the check gets no answer either: unconfirmed, everything kept", async () => {
      await signedIn();
      answers[key("DELETE", "/account")] = "offline";
      answers[key("GET", "/auth/check")] = "offline";
      expect(await deleteAccount(router())).toEqual({ ok: false, reason: "unconfirmed" });
      await stillMike();
    });

    test("a 503 without our JSON detail (the proxy during a deploy) is no answer: the check runs (M4)", async () => {
      await signedIn();
      answers[key("DELETE", "/account")] = new Response("<html>503 Service Unavailable</html>", { status: 503, headers: { "Content-Type": "text/html" } });
      answers[key("GET", "/auth/check")] = reply(401, { detail: "This device is no longer registered" });
      expect(await deleteAccount(router())).toEqual({ ok: true, appleRevoked: null });
      expect(calls.map((c) => c.path)).toEqual(["/account", "/auth/check"]);
      expect(getToken()).toBeNull();
    });

    test("the check says the token expired: renewed, and the DELETE goes once more with the new token", async () => {
      await signedIn();
      answers[key("DELETE", "/account")] = "offline";
      answers[key("GET", "/auth/check")] = reply(401, { detail: "Invalid or expired token" });
      answers[key("POST", "/auth/refresh")] = reply(200, { access_token: "ACC2", refresh_token: "REF2" });
      let n = 0;
      const call = jest.fn(async () => {
        if (++n === 1) return api.deleteAccount();
        return { ...DELETED, apple_revoked: true };
      });
      expect(await deleteAccount(router(), call)).toEqual({ ok: true, appleRevoked: true });
      expect(call).toHaveBeenCalledTimes(2);
    });

    test("the DELETE waits 60 s before it counts as no answer", async () => {
      expect(ACCOUNT_DELETE_TIMEOUT_MS).toBe(60000);
    });

    test("no other request goes out while the check runs", async () => {
      await signedIn();
      let answer: (r: Response) => void = () => {};
      answers[key("DELETE", "/account")] = "offline";
      answers[key("GET", "/auth/check")] = new Promise<Response>((res) => (answer = res));
      const deleting = deleteAccount(router());
      await new Promise((r) => setTimeout(r, 0));
      const other = api.memory().catch(() => null);
      await new Promise((r) => setTimeout(r, 20));
      expect(calls.map((c) => c.path)).toEqual(["/account", "/auth/check"]);
      answer(reply(401, { detail: "This device is no longer registered" }));
      expect((await deleting).ok).toBe(true);
      await other;
      expect(calls[2]).toEqual({ path: "/memory", method: "GET", auth: undefined });
    });
  });

  test("while the DELETE is on the wire no other request goes out; on failure they go with the same token", async () => {
    await signedIn();
    let answer: (r: Response) => void = () => {};
    answers[key("DELETE", "/account")] = new Promise<Response>((res) => (answer = res));
    const deleting = deleteAccount(router());
    await new Promise((r) => setTimeout(r, 0));
    const other = api.memory();
    await new Promise((r) => setTimeout(r, 20));
    expect(calls.map((c) => c.path)).toEqual(["/account"]);
    answer(reply(503, { detail: "x" }));
    expect(await deleting).toEqual({ ok: false, reason: "not_removed" });
    answers[key("GET", "/memory")] = reply(200, { turns: [] });
    await other;
    expect(calls).toEqual([
      { path: "/account", method: "DELETE", auth: "Bearer ACC" },
      { path: "/memory", method: "GET", auth: "Bearer ACC" },
    ]);
  });

  test("while the DELETE is on the wire no other request goes out; on success they go without the token", async () => {
    await signedIn();
    let answer: (r: Response) => void = () => {};
    answers[key("DELETE", "/account")] = new Promise<Response>((res) => (answer = res));
    const deleting = deleteAccount(router());
    await new Promise((r) => setTimeout(r, 0));
    const other = api.memory().catch(() => null);
    await new Promise((r) => setTimeout(r, 20));
    expect(calls.map((c) => c.path)).toEqual(["/account"]);
    answer(reply(200, { ok: true, apple_revoked: true }));
    expect((await deleting).ok).toBe(true);
    await other;
    expect(calls[1]).toEqual({ path: "/memory", method: "GET", auth: undefined });
  });

  test("an expired access token: renewed outside the hold, and the delete goes once more with the new one", async () => {
    await signedIn();
    let n = 0;
    const call = jest.fn(async () => {
      n++;
      if (n === 1) return api.deleteAccount(); // answered 401 below
      return { ...DELETED, apple_revoked: null };
    });
    answers[key("DELETE", "/account")] = reply(401, { detail: "Token expired" });
    answers[key("POST", "/auth/renew")] = reply(401, { detail: "Token expired" });
    answers[key("POST", "/auth/refresh")] = reply(200, { access_token: "ACC2", refresh_token: "REF2" });
    expect(await deleteAccount(router(), call)).toEqual({ ok: true, appleRevoked: null });
    expect(call).toHaveBeenCalledTimes(2);
    expect(calls.map((c) => c.path)).toContain("/auth/refresh");
  });
});

describe("one wipe at a time (review of #173, M1)", () => {
  test("two deletes at once (a second truck screen): one DELETE, both get its result, Delete my data is busy, nothing else goes out meanwhile", async () => {
    await signedIn();
    let answer: (r: Response) => void = () => {};
    answers[key("DELETE", "/account")] = new Promise<Response>((res) => (answer = res));
    const first = deleteAccount(router());
    await new Promise((r) => setTimeout(r, 0));
    expect(wipeRunning()).toBe(true);
    const second = deleteAccount(router());
    const third = deleteMyData(router());
    const other = api.memory().catch(() => null);
    await new Promise((r) => setTimeout(r, 20));
    expect(calls).toEqual([{ path: "/account", method: "DELETE", auth: "Bearer ACC" }]);
    answer(reply(200, { ok: true, apple_revoked: null }));
    expect(await first).toEqual({ ok: true, appleRevoked: null });
    expect(await second).toEqual({ ok: true, appleRevoked: null });
    expect(await third).toBe("busy"); // never the result of a wipe it did not ask for
    await other;
    expect(wipeRunning()).toBe(false);
    expect(calls.filter((c) => c.method === "DELETE")).toHaveLength(1);
    expect(calls.map((c) => c.path)).not.toContain("/memory/history");
  });

  test("Delete account while Delete my data runs: busy, nothing sent for it, the data wipe ends on its own", async () => {
    await signedIn("code");
    let answer: (r: Response) => void = () => {};
    answers[key("DELETE", "/memory")] = new Promise<Response>((res) => (answer = res));
    const data = deleteMyData(router());
    await new Promise((r) => setTimeout(r, 0));
    expect(await deleteAccount(router())).toEqual({ ok: false, reason: "busy" });
    expect(calls.map((c) => `${c.method} ${c.path}`)).toEqual(["DELETE /memory"]);
    answer(reply(200, { deleted: 1 }));
    expect(await data).toBe("done");
  });

  test("Sign out while Delete my data is on the wire: the hold stays until the DELETE settles (re-review, L-2)", async () => {
    await signedIn("code");
    let answer: (r: Response) => void = () => {};
    answers[key("DELETE", "/memory")] = new Promise<Response>((res) => (answer = res));
    const data = deleteMyData(router());
    await new Promise((r) => setTimeout(r, 0));
    await signedOutReset();
    expect(tokenHold()).not.toBeNull(); // forgetIdentity did not take down a hold it did not put up
    const other = api.memory().catch(() => null);
    await new Promise((r) => setTimeout(r, 20));
    expect(calls.map((c) => `${c.method} ${c.path}`)).toEqual(["DELETE /memory"]);
    answer(reply(200, { deleted: 1 }));
    await data;
    await other;
    expect(tokenHold()).toBeNull();
    expect(calls.slice(1).every((c) => c.auth === undefined)).toBe(true);
  });

  test("holdToken: a second caller cannot take down a hold it did not put up", async () => {
    await signedIn();
    const release = holdToken();
    const other = holdToken();
    other();
    expect(tokenHold()).not.toBeNull();
    release();
    expect(tokenHold()).toBeNull();
  });
});

describe("Delete my data with no answer (review of #173, L-4)", () => {
  test("waits 60 s before it counts as no answer", () => {
    expect(WIPE_DATA_TIMEOUT_MS).toBe(60000);
  });

  test.each([
    ["session_closed", { detail: "This device is no longer registered", code: "session_closed" }],
    ["account_gone", { detail: "This device is no longer registered", code: "account_gone" }],
  ])("the check says %s: signed out with the notice, never \"deleted\"", async (_, body) => {
    await signedIn("code");
    answers[key("DELETE", "/memory")] = "offline";
    answers[key("GET", "/auth/check")] = reply(401, body);
    expect(await deleteMyData(router())).toBe("signed_out");
    expect(getToken()).toBeNull();
    expect(signInNotice()).toBe("signed_out");
    expect(useLangStore.getState().chosen).toBe(true);
  });

  test("the check says the token expired: renewed, and the DELETE goes once more with the new token", async () => {
    await signedIn("code");
    let n = 0;
    const call = jest.fn(async () => {
      if (++n === 1) throw new TypeError("Network request failed");
      return { deleted: 1 };
    });
    answers[key("GET", "/auth/check")] = reply(401, { detail: "Invalid or expired token" });
    answers[key("POST", "/auth/refresh")] = reply(200, { access_token: "ACC2", refresh_token: "REF2" });
    expect(await deleteMyData(router(), call)).toBe("done");
    expect(call).toHaveBeenCalledTimes(2);
    expect(getToken()).toBeNull();
  });

  test("the check gets no answer either: unconfirmed, everything kept", async () => {
    await signedIn("code");
    answers[key("DELETE", "/memory")] = reply(504, "gateway timeout");
    answers[key("GET", "/auth/check")] = "offline";
    expect(await deleteMyData(router())).toBe("unconfirmed");
    expect(getToken()).toBe("ACC");
    expect(getTruckProfile().height_in).toBe(168);
  });

  test("an answer with an error is failed, with no check", async () => {
    await signedIn("code");
    answers[key("DELETE", "/memory")] = reply(500, { detail: "Internal Server Error" });
    expect(await deleteMyData(router())).toBe("failed");
    expect(calls.map((c) => c.path)).toEqual(["/memory"]);
  });
});

describe("a rejected token while the DELETE is on the wire (re-review of #173, L-3)", () => {
  const b64url = (v: string) => Buffer.from(v).toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  const JWT = `${b64url('{"alg":"HS256"}')}.${b64url(JSON.stringify({ sub: "acct-mike", sid: "s1", exp: Math.floor(Date.now() / 1000) + 3600 }))}.sig`;
  const PLACE = { id: "p1", name: "Pilot", category: "truck_stop", lat: 38.55, lng: -121.42, tags: [] };
  const ROUTE = { geometry: [[38.58, -121.49]], distance_m: 1, duration_s: 1, distance_mi: 0, duration_min: 0, summary: "", source: "here" };

  test("the wipe decides: no trip set aside, no notice, no sign-out under the DELETE, and a fresh install after it", async () => {
    await signedIn();
    await setToken(JWT, "driver", "account", "REF");
    await saveActiveRoute({ place: PLACE, route: ROUTE, trip_id: "t1" } as never, Date.now(), true);
    useTripStore.getState().setDriving(true);
    calls = [];
    // A renewal already under way when the driver confirmed the delete; the server turns it down mid-DELETE.
    let refuse: (r: Response) => void = () => {};
    answers[key("POST", "/auth/refresh")] = new Promise<Response>((res) => (refuse = res));
    const renewal = after401(JWT);
    await new Promise((r) => setTimeout(r, 0));
    let answer: (r: Response) => void = () => {};
    answers[key("DELETE", "/account")] = new Promise<Response>((res) => (answer = res));
    const deleting = deleteAccount(router());
    await new Promise((r) => setTimeout(r, 0));
    expect(calls.map((c) => `${c.method} ${c.path}`)).toContain("DELETE /account");
    refuse(reply(401, { detail: "Invalid or expired refresh token" }));
    await new Promise((r) => setTimeout(r, 20));
    expect(getToken()).toBe(JWT); // nothing signed the phone out under the DELETE
    expect(signInNotice()).toBeNull();
    expect(await AsyncStorage.getItem(HELD_TRIP_KEY)).toBeNull();
    answer(reply(200, { ...DELETED }));
    expect((await deleting).ok).toBe(true);
    expect(await renewal).toBe(false);
    expect(getToken()).toBeNull();
    expect(signInNotice()).toBeNull();
    expect(await AsyncStorage.getItem(HELD_TRIP_KEY)).toBeNull();
    expect(useLangStore.getState().chosen).toBe(false);
  });
});

describe("Clear memory", () => {
  // Emulator QA of #173: an access-code sign-in sent DELETE /api/memory ("Delete my data"), which took the stored
  // truck on the server while the phone kept showing it as synced.
  test.each(["account", "code", "device"] as const)("%s: only the conversations go (DELETE /api/memory/history); the truck stays", async (via) => {
    await signedIn(via);
    const truckOnDisk = await AsyncStorage.getItem("truck_profile_v1");
    expect(truckOnDisk).not.toBeNull();
    answers[key("DELETE", "/memory/history")] = reply(200, { deleted: 4, facts: true });
    await clearConversations();
    expect(calls).toEqual([{ path: "/memory/history", method: "DELETE", auth: "Bearer ACC" }]);
    expect(calls.some((c) => c.method === "DELETE" && c.path === "/memory")).toBe(false);
    expect(getTruckProfile().height_in).toBe(168);
    expect(await AsyncStorage.getItem("truck_profile_v1")).toBe(truckOnDisk);
    expect(getToken()).toBe("ACC");
  });

  test("a refusal reaches the history screen as an error and still sends no DELETE /api/memory", async () => {
    await signedIn("code");
    answers[key("DELETE", "/memory/history")] = reply(503, { detail: "Memory is unavailable right now" });
    await expect(clearConversations()).rejects.toBeTruthy();
    expect(calls.filter((c) => c.method === "DELETE").map((c) => c.path)).toEqual(["/memory/history"]);
    expect(getTruckProfile().height_in).toBe(168);
  });
});
