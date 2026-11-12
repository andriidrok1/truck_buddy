// The account on the truck screen (APP-3): sign out, sign out of all phones, delete the account, and "Clear memory"
// per identity. The server is a fetch stand-in that answers like backend services/account_api.py.
import AsyncStorage from "@react-native-async-storage/async-storage";

import { ACCOUNT_DELETE_TIMEOUT_MS, api } from "../api";
import { getToken, holdToken, loadToken, setToken, tokenHold } from "../auth";
import { CONSENT_KEY, giveConsent } from "../consent";
import { saveProfile } from "../data/profile";
import { DEFAULT_TRUCK, getTruckProfile } from "../hooks/useTruckProfile";
import { loadLang, setLang, useLangStore } from "../i18n";
import { resetRenewalForTests } from "../net/tokenRenewal";
import { queryClient } from "../query-client";
import { getAccount, saveAccount, signInNotice } from "../state/account";
import { clearConversations, deleteAccount, LOGOUT_WAIT_MS, signOut, signOutEverywhere } from "../state/accountActions";
import { deleteMyData, wipeRunning } from "../state/firstLaunch";
import { markIntroSeen } from "../state/intro";
import { storage } from "../utils/storage";
import { __resetWipeGate } from "../utils/storage/pendingWipe";

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
async function signedIn(via: "account" | "code" = "account") {
  await AsyncStorage.clear();
  mockSecure.clear();
  __resetWipeGate();
  resetRenewalForTests();
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
  test("online: this phone's session is closed, then the phone forgets the driver and keeps the language and consent", async () => {
    await signedIn();
    await signOut();
    expect(calls).toEqual([{ path: "/auth/logout", method: "POST", auth: "Bearer ACC" }]);
    expect(getToken()).toBeNull();
    expect(getTruckProfile()).toEqual(DEFAULT_TRUCK);
    expect(getAccount()).toBeNull();
    expect(useLangStore.getState().chosen).toBe(true);
    expect(await storage.getItem(CONSENT_KEY, false as boolean)).toBe(true);
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
    test("our 401: the sessions are closed, this phone signs out as the driver asked (no notice)", async () => {
      await signedIn();
      answers[key("POST", "/auth/logout-all")] = "offline";
      answers[key("GET", "/auth/check")] = reply(401, { detail: "This session was signed out" });
      expect(await signOutEverywhere()).toBe("done");
      expect(calls.map((c) => c.path)).toEqual(["/auth/logout-all", "/auth/check"]);
      expect(calls[1].auth).toBe("Bearer ACC");
      expect(getToken()).toBeNull();
      expect(signInNotice()).toBeNull();
      expect(useLangStore.getState().chosen).toBe(true);
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
    test("no longer registered: a deletion that already went through, the phone resets as on a 200, no renewal", async () => {
      await signedIn();
      answers[key("DELETE", "/account")] = reply(401, { detail: "This device is no longer registered" });
      const r = router();
      expect(await deleteAccount(r)).toEqual({ ok: true, appleRevoked: null });
      expect(calls.map((c) => `${c.method} ${c.path}`)).toEqual(["DELETE /account"]);
      expect(r.dismissAll).toHaveBeenCalled();
      expect(getToken()).toBeNull();
      expect(useLangStore.getState().chosen).toBe(false);
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

    test("an expired token, and the retry hears no longer registered: the phone resets", async () => {
      await signedIn();
      let n = 0;
      const call = jest.fn(async () => {
        n++;
        if (n === 1) return api.deleteAccount();
        answers[key("DELETE", "/account")] = reply(401, { detail: "This device is no longer registered" });
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
    test("401 no longer registered: the deletion went through, the phone resets as on a 200", async () => {
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
  test("two deletes at once (a second truck screen): one DELETE, both get its result, nothing else goes out meanwhile", async () => {
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
    expect(await third).toBe(true);
    await other;
    expect(wipeRunning()).toBe(false);
    expect(calls.filter((c) => c.method === "DELETE")).toHaveLength(1);
    expect(calls.map((c) => c.path)).not.toContain("/memory/history");
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

describe("Clear memory", () => {
  test("an account clears only the conversations (DELETE /api/memory/history); the truck stays", async () => {
    await signedIn("account");
    answers[key("DELETE", "/memory/history")] = reply(200, { deleted: 4, facts: true });
    await clearConversations();
    expect(calls).toEqual([{ path: "/memory/history", method: "DELETE", auth: "Bearer ACC" }]);
    expect(getTruckProfile().height_in).toBe(168);
    expect(getToken()).toBe("ACC");
  });

  test("an access-code sign-in keeps the older DELETE /api/memory", async () => {
    await signedIn("code");
    answers[key("DELETE", "/memory")] = reply(200, { deleted: 4 });
    await clearConversations();
    expect(calls).toEqual([{ path: "/memory", method: "DELETE", auth: "Bearer ACC" }]);
    expect(getTruckProfile().height_in).toBe(168);
  });
});
