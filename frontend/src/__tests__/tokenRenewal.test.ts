import { api, registerDevice } from "../api";
import { getRefreshToken, getToken, setToken } from "../auth";
import { after401, renewOnStart, renewToken, RENEW_BEFORE_MS, resetRenewalForTests, setIdentityRejectedHandler, tokenExpiry } from "../net/tokenRenewal";

// The Keychain native module is missing under jest; a map stands in for it.
const mockSecure = new Map<string, string>();
/** storage.secure* keeps values JSON-encoded. */
const stored = (k: string) => (mockSecure.has(k) ? JSON.parse(mockSecure.get(k)!) : undefined);
/** Every Keychain write in order, with its options; `mockFailWrite` makes one key refuse to be written (a kill mid-way). */
const mockWrites: { key: string; options: unknown }[] = [];
let mockFailWrite: string | null = null;
jest.mock("expo-secure-store", () => ({
  AFTER_FIRST_UNLOCK: "after-first-unlock",
  getItemAsync: async (k: string) => mockSecure.get(k) ?? null,
  setItemAsync: async (k: string, v: string, options?: unknown) => {
    mockWrites.push({ key: k, options });
    if (k === mockFailWrite) throw new Error("killed before this write");
    mockSecure.set(k, v);
  },
  deleteItemAsync: async (k: string) => void mockSecure.delete(k),
}));

const b64url = (s: string) => Buffer.from(s).toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
/** A JWT-shaped token expiring `inMs` from now (the signature is the server's business). */
const jwtExp = (inMs: number, tag = "a") =>
  `${b64url('{"alg":"HS256"}')}.${b64url(JSON.stringify({ sub: `drv_${tag}`, exp: Math.floor((Date.now() + inMs) / 1000) }))}.sig`;

type Reply = { status: number; body?: unknown } | "offline" | "proxy403" | "proxy404" | "proxy401";
/** `body` left out: a 401 is ours (it carries the `detail` every 401 of the API has), anything else is empty. */
const json = (status: number, body?: unknown): Response => {
  const res = {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: () => "application/json" },
    json: async () => body ?? (status === 401 ? { detail: "Invalid or expired token" } : {}),
    clone: () => res,
  };
  return res as unknown as Response;
};
/** What a proxy or a captive portal answers: not JSON at all. */
const notOurs = (status: number): Response => {
  const res = { ok: false, status, headers: { get: () => "text/html" }, json: async () => { throw new SyntaxError("Unexpected token <"); }, clone: () => res };
  return res as unknown as Response;
};

let calls: { url: string; init: RequestInit }[] = [];
function mockFetch(routes: Record<string, Reply | Reply[]>) {
  calls = [];
  const queues = Object.fromEntries(Object.entries(routes).map(([k, v]) => [k, Array.isArray(v) ? [...v] : [v]]));
  global.fetch = jest.fn(async (url: string, init: RequestInit) => {
    calls.push({ url, init });
    const key = Object.keys(queues).find((k) => url.endsWith(k));
    if (!key) throw new Error(`unexpected ${url}`);
    const q = queues[key];
    const r = q.length > 1 ? q.shift()! : q[0];
    if (r === "offline") throw new TypeError("Network request failed");
    if (typeof r === "string") return notOurs(Number(r.slice(5)));
    return json(r.status, r.body);
  }) as unknown as typeof fetch;
}
const hits = (path: string) => calls.filter((c) => c.url.endsWith(path));
const bearer = (c: { init: RequestInit }) => (c.init.headers as Record<string, string>).Authorization;

beforeEach(async () => {
  mockSecure.clear();
  mockWrites.length = 0;
  mockFailWrite = null;
  resetRenewalForTests();
  setIdentityRejectedHandler(null);
  await setToken(null);
});

test("tokenExpiry reads exp from a JWT and gives up on anything else", () => {
  const t = jwtExp(3600_000);
  expect(Math.abs(tokenExpiry(t)! - (Date.now() + 3600_000))).toBeLessThan(2000);
  expect(tokenExpiry("garbage")).toBeNull();
  expect(tokenExpiry(null)).toBeNull();
  expect(tokenExpiry("a.!!!.c")).toBeNull();
});

test("sign-up keeps the refresh token in secure storage next to the access token, and it survives a restart", async () => {
  mockFetch({ "/auth/device": { status: 200, body: { access_token: "A1", refresh_token: "R1" } } });
  await registerDevice();
  expect(getToken()).toBe("A1");
  expect(getRefreshToken()).toBe("R1");
  expect(stored("auth_refresh")).toBe("R1");
  // a token without a refresh token drops the stale one
  await setToken("A2");
  expect(mockSecure.has("auth_refresh")).toBe(false);
  await setToken("A3", "driver", "device", "R3");
  await setToken(null);
  expect(mockSecure.has("auth_refresh")).toBe(false);
});

test("on start: a token near expiry is refreshed with the refresh token", async () => {
  await setToken(jwtExp(RENEW_BEFORE_MS / 2), "driver", "device", "R1");
  mockFetch({ "/auth/refresh": { status: 200, body: { access_token: "A2", refresh_token: "R2", role: "driver" } } });
  expect(await renewOnStart()).toBe("renewed");
  expect(JSON.parse(hits("/auth/refresh")[0].init.body as string)).toEqual({ refresh_token: "R1" });
  expect(getToken()).toBe("A2");
  expect(getRefreshToken()).toBe("R2");
  expect(stored("auth_token")).toBe("A2");
});

test("on start: a fresh token with a refresh token stored does nothing", async () => {
  await setToken(jwtExp(20 * 86400_000), "driver", "device", "R1");
  mockFetch({});
  expect(await renewOnStart()).toBeNull();
  expect(calls).toHaveLength(0);
});

test("on start: a build-2 phone (no refresh token) trades its expired token for a pair via /auth/renew", async () => {
  const old = jwtExp(-40 * 86400_000);
  await setToken(old);
  mockFetch({ "/auth/renew": { status: 200, body: { access_token: "A2", refresh_token: "R2", role: "driver" } } });
  expect(await renewOnStart()).toBe("renewed");
  expect(bearer(hits("/auth/renew")[0])).toBe(`Bearer ${old}`);
  expect(getToken()).toBe("A2");
  expect(getRefreshToken()).toBe("R2");
});

test("on start: a server without /auth/renew (404) never signs the phone out", async () => {
  const old = jwtExp(-1000);
  await setToken(old);
  mockFetch({ "/auth/renew": { status: 404 } });
  expect(await renewOnStart()).toBe("unavailable");
  expect(getToken()).toBe(old);
});

test("a 401 renews once and retries the request with the new token", async () => {
  await setToken("A1", "driver", "device", "R1");
  mockFetch({
    "/memory": [{ status: 401, body: { detail: "Invalid or expired token" } }, { status: 200, body: { turns: [] } }],
    "/auth/refresh": { status: 200, body: { access_token: "A2", refresh_token: "R2" } },
  });
  await expect(api.memory()).resolves.toEqual({ turns: [] });
  const mem = hits("/memory");
  expect(mem.map(bearer)).toEqual(["Bearer A1", "Bearer A2"]);
  expect(getToken()).toBe("A2");
});

test("a 401 while offline keeps the token and the refresh token: no new device", async () => {
  await setToken("A1", "driver", "device", "R1");
  mockFetch({ "/memory": { status: 401 }, "/auth/refresh": "offline" });
  await expect(api.memory()).rejects.toThrow("401");
  expect(getToken()).toBe("A1");
  expect(getRefreshToken()).toBe("R1");
  // a server error is not a verdict either
  mockFetch({ "/memory": { status: 401 }, "/auth/refresh": { status: 503 } });
  await expect(api.memory()).rejects.toThrow("401");
  expect(getToken()).toBe("A1");
});

test("a rejected refresh token falls back to /auth/renew with the access token", async () => {
  await setToken("A1", "driver", "device", "R-stale");
  mockFetch({
    "/memory": [{ status: 401 }, { status: 200, body: { turns: [] } }],
    "/auth/refresh": { status: 401 },
    "/auth/renew": { status: 200, body: { access_token: "A2", refresh_token: "R2" } },
  });
  await expect(api.memory()).resolves.toEqual({ turns: [] });
  expect(getToken()).toBe("A2");
});

test("refresh and renew both rejected: the token goes (the sign-in screen comes up), the request is not retried", async () => {
  await setToken("A1", "driver", "device", "R1");
  mockFetch({ "/memory": { status: 401 }, "/auth/refresh": { status: 401 }, "/auth/renew": { status: 401 } });
  await expect(api.memory()).rejects.toThrow("401");
  expect(hits("/memory")).toHaveLength(1);
  expect(getToken()).toBeNull();
  expect(mockSecure.has("auth_refresh")).toBe(false);
});

describe("an account session: the refresh token is single-use on the server", () => {
  /** Answers only when the test says so, so two callers can be in flight together. */
  function heldFetch() {
    calls = [];
    const waiting: ((r: Response) => void)[] = [];
    global.fetch = jest.fn((url: string, init: RequestInit) => {
      calls.push({ url, init });
      return new Promise<Response>((resolve) => waiting.push(resolve));
    }) as unknown as typeof fetch;
    return (status: number, body: unknown = {}) => waiting.shift()!(json(status, body));
  }
  const tick = () => new Promise((r) => setTimeout(r, 0));
  const paths = () => calls.map((c) => c.url.replace(/^.*\/api/, ""));
  const PAIR2 = { status: 200, body: { access_token: jwtExp(30 * 86400_000, "a2"), refresh_token: "R2", role: "driver" } };
  /** A cold start: fresh copies of the modules, the Keychain as it was left. */
  function restart() {
    jest.resetModules();
    /* eslint-disable @typescript-eslint/no-require-imports -- fresh module copies, as after a cold start */
    const auth = require("../auth") as typeof import("../auth");
    const renewal = require("../net/tokenRenewal") as typeof import("../net/tokenRenewal");
    /* eslint-enable @typescript-eslint/no-require-imports */
    return { auth, renewal };
  }

  test("while the access token is good the renewal goes through it, and the refresh token is not spent", async () => {
    const a1 = jwtExp(RENEW_BEFORE_MS / 2);
    await setToken(a1, "driver", "account", "R1");
    mockFetch({ "/auth/renew": PAIR2 });
    expect(await renewOnStart()).toBe("renewed");
    expect(paths()).toEqual(["/auth/renew"]);
    expect(bearer(calls[0])).toBe(`Bearer ${a1}`);
    expect(getRefreshToken()).toBe("R2");
    expect(stored("auth_via")).toBe("account"); // still an account sign-in after the trade
  });

  test("two callers at once make one network call", async () => {
    await setToken(jwtExp(RENEW_BEFORE_MS / 2), "driver", "account", "R1");
    const answer = heldFetch();
    const a = renewToken(); // a request's 401
    const b = renewOnStart(); // the app came to the foreground at the same moment
    const c = renewToken();
    await tick();
    expect(calls).toHaveLength(1);
    answer(200, PAIR2.body);
    expect(await Promise.all([a, b, c])).toEqual(["renewed", "renewed", "renewed"]);
    expect(calls).toHaveLength(1);
  });

  test("the access token has expired: the refresh token goes out, marked as sent on disk first, and the mark goes when the new pair is in", async () => {
    await setToken(jwtExp(-1000), "driver", "account", "R1");
    let markAtSend: unknown;
    calls = [];
    global.fetch = jest.fn(async (url: string, init: RequestInit) => {
      calls.push({ url, init });
      markAtSend = stored("auth_refresh_sent");
      return json(200, PAIR2.body);
    }) as unknown as typeof fetch;
    expect(await renewOnStart()).toBe("renewed");
    expect(paths()).toEqual(["/auth/refresh"]); // /auth/renew can only say no to an expired account token
    expect(markAtSend).toBe("R1");
    expect(mockSecure.has("auth_refresh_sent")).toBe(false);
    expect(getRefreshToken()).toBe("R2");
  });

  test("the new pair is written refresh token first, and the auth keys can be read on a locked phone", async () => {
    await setToken("A1", "driver", "account", "R1");
    const keys = mockWrites.map((w) => w.key);
    expect(keys.indexOf("auth_refresh")).toBeLessThan(keys.indexOf("auth_token"));
    for (const w of mockWrites) expect(w.options).toEqual({ keychainAccessible: "after-first-unlock" });
  });

  test("the answer to /auth/renew is lost and the app is killed: the next start renews through the access token again, the refresh token never goes out", async () => {
    const a1 = jwtExp(RENEW_BEFORE_MS / 2);
    await setToken(a1, "driver", "account", "R1");
    mockFetch({ "/auth/renew": "offline" }); // the server may well have rotated; the phone never heard
    expect(await renewOnStart()).toBe("unavailable");
    expect(getToken()).toBe(a1);

    const { auth, renewal } = restart();
    await auth.loadToken();
    const signOut = jest.fn(async () => {});
    renewal.setIdentityRejectedHandler(signOut);
    mockFetch({ "/auth/renew": PAIR2 });
    expect(await renewal.renewOnStart()).toBe("renewed");
    expect(paths()).toEqual(["/auth/renew"]);
    expect(signOut).not.toHaveBeenCalled();
    expect(auth.getRefreshToken()).toBe("R2");
  });

  test("a refresh token was sent, the answer was lost, the app was killed: after the restart it is not the first thing tried", async () => {
    // A request got our 401 on the access token (the clock, a deploy), so the refresh token went out; no answer.
    const a1 = jwtExp(RENEW_BEFORE_MS / 2);
    await setToken(a1, "driver", "account", "R1");
    mockFetch({ "/auth/refresh": "offline" });
    expect(await renewToken(true)).toBe("unavailable");
    expect(stored("auth_refresh_sent")).toBe("R1"); // survives the kill

    const { auth, renewal } = restart();
    await auth.loadToken();
    expect(auth.refreshMaybeSpent()).toBe(true);
    mockFetch({ "/auth/renew": PAIR2, "/auth/refresh": { status: 401 } });
    expect(await renewal.renewOnStart()).toBe("renewed");
    expect(paths()).toEqual(["/auth/renew"]); // the possibly spent R1 stayed home
    expect(mockSecure.has("auth_refresh_sent")).toBe(false);
  });

  test("...and with nothing else left (the access token is dead too) it is tried once more rather than signing the driver out unheard", async () => {
    await setToken(jwtExp(-1000), "driver", "account", "R1");
    mockFetch({ "/auth/refresh": "offline" }); // perhaps it never left the phone at all
    expect(await renewOnStart()).toBe("unavailable");
    const { auth, renewal } = restart();
    await auth.loadToken();
    mockFetch({ "/auth/refresh": PAIR2 });
    expect(await renewal.renewOnStart()).toBe("renewed"); // it had not been spent
    expect(auth.getRefreshToken()).toBe("R2");
  });

  test("killed between the two writes of a new pair: the new refresh token with the old access token still renews", async () => {
    const a1 = jwtExp(RENEW_BEFORE_MS / 2);
    await setToken(a1, "driver", "account", "R1");
    mockFetch({ "/auth/renew": PAIR2 });
    mockFailWrite = "auth_token"; // the refresh token got to disk, the access token did not
    await renewOnStart();
    mockFailWrite = null;
    expect(stored("auth_refresh")).toBe("R2");
    expect(stored("auth_token")).toBe(a1);

    const { auth, renewal } = restart();
    await auth.loadToken();
    const signOut = jest.fn(async () => {});
    renewal.setIdentityRejectedHandler(signOut);
    mockFetch({ "/auth/renew": { status: 200, body: { access_token: jwtExp(30 * 86400_000, "a3"), refresh_token: "R3" } } });
    expect(await renewal.renewOnStart()).toBe("renewed");
    expect(bearer(calls[0])).toBe(`Bearer ${a1}`);
    expect(paths()).toEqual(["/auth/renew"]);
    expect(signOut).not.toHaveBeenCalled();
  });

  test("a 502 from the edge after the origin rotated: not a verdict, the mark stays, and the next try goes through the access token", async () => {
    const a1 = jwtExp(RENEW_BEFORE_MS / 2);
    await setToken(a1, "driver", "account", "R1");
    const signOut = jest.fn(async () => {});
    setIdentityRejectedHandler(signOut);
    mockFetch({ "/memory": { status: 401 }, "/auth/refresh": { status: 502 } });
    await expect(api.memory()).rejects.toThrow("401");
    expect(signOut).not.toHaveBeenCalled();
    expect(getToken()).toBe(a1);
    expect(getRefreshToken()).toBe("R1");
    expect(stored("auth_refresh_sent")).toBe("R1");
    // a timeout page, a sleeping host: the same
    mockFetch({ "/auth/renew": { status: 504 } });
    expect(await renewOnStart()).toBe("unavailable");
    expect(hits("/auth/refresh")).toHaveLength(0);
    mockFetch({ "/auth/renew": PAIR2 });
    expect(await renewOnStart()).toBe("renewed");
    expect(hits("/auth/refresh")).toHaveLength(0);
  });

  test("a 403 or 404 from a proxy never signs the phone out, on either path", async () => {
    const a1 = jwtExp(RENEW_BEFORE_MS / 2);
    await setToken(a1, "driver", "account", "R1");
    const signOut = jest.fn(async () => {});
    setIdentityRejectedHandler(signOut);
    for (const edge of ["proxy403", "proxy404"] as const) {
      mockFetch({ "/memory": { status: 401 }, "/auth/refresh": edge, "/auth/renew": edge });
      await expect(api.memory()).rejects.toThrow("401");
      expect(await renewOnStart()).toBe("unavailable");
      // our own 403/404/422 are no verdict about the driver either
      mockFetch({ "/memory": { status: 401 }, "/auth/refresh": { status: 422 }, "/auth/renew": { status: 404 } });
      await expect(api.memory()).rejects.toThrow("401");
      expect(await renewOnStart()).toBe("unavailable");
    }
    expect(signOut).not.toHaveBeenCalled();
    expect(getToken()).toBe(a1);
    expect(getRefreshToken()).toBe("R1");
    expect(mockSecure.has("auth_refresh_sent")).toBe(false); // the request never got as far as the rotation
  });

  test("a 401 that is not ours (a captive portal) does not even start a renewal", async () => {
    await setToken("A1", "driver", "account", "R1");
    mockFetch({ "/memory": "proxy401" });
    await expect(api.memory()).rejects.toThrow("401");
    expect(paths()).toEqual(["/memory"]);
    expect(getToken()).toBe("A1");
  });

  test("a refresh token the server turned down is dropped and never sent again", async () => {
    await setToken(jwtExp(RENEW_BEFORE_MS / 2), "driver", "account", "R-spent");
    mockFetch({ "/auth/refresh": { status: 401, body: { detail: "Invalid or expired refresh token" } } });
    expect(await renewToken(true)).toBe("rejected");
    expect(getRefreshToken()).toBeNull();
    expect(mockSecure.has("auth_refresh")).toBe(false);
    expect(mockSecure.has("auth_refresh_sent")).toBe(false);
  });

  test("rejected for good (our 401 on the request and on the refresh token): no anonymous device, the sign-out handler runs once for a burst", async () => {
    await setToken(jwtExp(RENEW_BEFORE_MS / 2), "driver", "account", "R1");
    const signOut = jest.fn(async () => {
      await setToken(null);
    });
    setIdentityRejectedHandler(signOut);
    mockFetch({
      "/memory": { status: 401, body: { detail: "This device is no longer registered" } },
      "/auth/refresh": { status: 401, body: { detail: "Invalid or expired refresh token" } },
    });
    const results = await Promise.allSettled([api.memory(), api.memory(), api.memory()]);
    expect(results.map((r) => r.status)).toEqual(["rejected", "rejected", "rejected"]);
    expect(signOut).toHaveBeenCalledTimes(1);
    expect(hits("/auth/device")).toHaveLength(0);
    expect(hits("/auth/access-code")).toHaveLength(0);
    expect(hits("/auth/refresh")).toHaveLength(1);
    expect(getToken()).toBeNull();
    expect(mockSecure.has("auth_via")).toBe(false);
  });

  test("a handler that throws still leaves the phone signed out", async () => {
    await setToken("A1", "driver", "account", "R1");
    setIdentityRejectedHandler(async () => {
      throw new Error("disk full");
    });
    mockFetch({ "/auth/refresh": { status: 401 } });
    expect(await after401("A1")).toBe(false);
    expect(getToken()).toBeNull();
  });

  test("offline is not a verdict: the account stays signed in and the handler is not called", async () => {
    await setToken("A1", "driver", "account", "R1");
    const signOut = jest.fn(async () => {});
    setIdentityRejectedHandler(signOut);
    mockFetch({ "/auth/refresh": "offline", "/auth/renew": "offline" });
    expect(await after401("A1")).toBe(false);
    expect(signOut).not.toHaveBeenCalled();
    expect(getToken()).toBe("A1");
  });
});

test("a burst of 401s shares one renewal", async () => {
  await setToken("A1", "driver", "device", "R1");
  mockFetch({
    "/memory": [{ status: 401 }, { status: 401 }, { status: 200, body: { turns: [] } }],
    "/auth/refresh": { status: 200, body: { access_token: "A2", refresh_token: "R2" } },
  });
  await Promise.all([api.memory(), api.memory()]);
  expect(hits("/auth/refresh")).toHaveLength(1);
  expect(getToken()).toBe("A2");
});

test("a tester keeps the tester role and the code sign-in across a refresh", async () => {
  await setToken("T1", "tester", "code", "R1");
  mockFetch({ "/auth/refresh": { status: 200, body: { access_token: "T2", refresh_token: "R2", role: "tester" } } });
  expect(await renewToken()).toBe("renewed");
  expect(stored("auth_role")).toBe("tester");
  expect(stored("auth_via")).toBe("code");
});

test("loadToken reads the stored refresh token", async () => {
  mockSecure.set("auth_token", JSON.stringify("A9"));
  mockSecure.set("auth_refresh", JSON.stringify("R9"));
  jest.resetModules();
  // eslint-disable-next-line @typescript-eslint/no-require-imports -- a fresh module copy, as after a cold start
  const fresh = require("../auth") as typeof import("../auth");
  await fresh.loadToken();
  expect(fresh.getRefreshToken()).toBe("R9");
});
