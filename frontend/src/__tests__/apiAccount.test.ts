// POST /api/auth/apple, /api/auth/google and /api/auth/logout as the app calls them (backend services/account_api.py).
import { logout, SignInError, signInApple, signInGoogle } from "../api";
import { getRefreshToken, getToken, setToken, signedInVia } from "../auth";

const mockSecure = new Map<string, string>();
jest.mock("expo-secure-store", () => ({
  getItemAsync: async (k: string) => mockSecure.get(k) ?? null,
  setItemAsync: async (k: string, v: string) => void mockSecure.set(k, v),
  deleteItemAsync: async (k: string) => void mockSecure.delete(k),
}));

type Call = { path: string; method: string; headers: Record<string, string>; body: unknown };
let calls: Call[] = [];
const realFetch = global.fetch;
const reply = (status: number, body: unknown = {}, type = "application/json") => new Response(typeof body === "string" ? body : JSON.stringify(body), { status, headers: { "Content-Type": type } });
function server(r: Response | "offline") {
  calls = [];
  global.fetch = jest.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ path: String(url).replace("https://ci.invalid/api", ""), method: init?.method ?? "GET", headers: (init?.headers ?? {}) as Record<string, string>, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    if (r === "offline") throw new TypeError("Network request failed");
    return r.clone();
  }) as typeof fetch;
}
const PAIR = { access_token: "ACC", refresh_token: "REF", token_type: "bearer", role: "driver", expires_in: 2592000, new_account: true, has_profile: false, name: null, email: null };
const APPLE = { user: "apple-user", identityToken: "id.token", rawNonce: "a".repeat(64), authorizationCode: "code", name: "Mike Johnson" };

beforeEach(async () => {
  mockSecure.clear();
  await setToken(null);
});
afterAll(() => {
  global.fetch = realFetch;
});

test("Apple: the request the server expects, with the platform; the pair is stored as an account sign-in", async () => {
  server(reply(200, PAIR));
  const out = await signInApple(APPLE);
  expect(calls).toHaveLength(1);
  expect(calls[0]).toMatchObject({ path: "/auth/apple", method: "POST" });
  expect(calls[0].body).toEqual({ identity_token: "id.token", nonce: "a".repeat(64), authorization_code: "code", name: "Mike Johnson", platform: "ios" });
  expect(calls[0].headers.Authorization).toBeUndefined();
  expect(calls[0].headers["X-Lang"]).toBe("en");
  expect(out.new_account).toBe(true);
  expect(getToken()).toBe("ACC");
  expect(getRefreshToken()).toBe("REF");
  expect(signedInVia()).toBe("account");
  expect(JSON.parse(mockSecure.get("auth_via")!)).toBe("account");
  expect(JSON.parse(mockSecure.get("auth_refresh")!)).toBe("REF");
});

test("Apple without a name or a code (a later authorization): the fields are left out, not sent as null", async () => {
  server(reply(200, PAIR));
  await signInApple({ ...APPLE, authorizationCode: null, name: null });
  expect(calls[0].body).toEqual({ identity_token: "id.token", nonce: "a".repeat(64), platform: "ios" });
});

test("Google: the id token and the platform", async () => {
  server(reply(200, PAIR));
  await signInGoogle("google.id.token");
  expect(calls[0]).toMatchObject({ path: "/auth/google", method: "POST", body: { id_token: "google.id.token", platform: "ios" } });
  expect(signedInVia()).toBe("account");
});

test("the preparation runs after the server's answer and before the token is in", async () => {
  server(reply(200, { ...PAIR, has_profile: true, new_account: false }));
  const seen: unknown[] = [];
  await signInGoogle("t", async (r) => {
    await new Promise((res) => setTimeout(res, 5));
    seen.push([r.has_profile, getToken()]);
  });
  expect(seen).toEqual([[true, null]]);
  expect(getToken()).toBe("ACC");
});

test("a preparation that fails leaves the phone signed out", async () => {
  server(reply(200, PAIR));
  await expect(
    signInGoogle("t", () => {
      throw new Error("disk full");
    }),
  ).rejects.toThrow("disk full");
  expect(getToken()).toBeNull();
});

test("refused, unreachable or not an answer: a SignInError with the status, nothing stored", async () => {
  for (const [r, status] of [
    [reply(401, { detail: "Sign-in failed" }), 401],
    [reply(429, { detail: "Too many attempts. Try again in a few minutes." }), 429],
    [reply(503, { detail: "Sign-in is not configured" }), 503],
    ["offline", 0],
    [reply(200, "<html>sleeping host</html>", "text/html"), 0],
    [reply(200, { access_token: "ACC" }), 0], // no refresh token: not a session the app can keep
  ] as const) {
    server(r);
    const err = await signInGoogle("t").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(SignInError);
    expect((err as SignInError).status).toBe(status);
    expect(getToken()).toBeNull();
  }
});

describe("logout", () => {
  test("closes this phone's session with its token and leaves the local sign-out to the caller", async () => {
    await setToken("ACC", "driver", "account", "REF");
    server(reply(200, { ok: true }));
    expect(await logout()).toBe(true);
    expect(calls).toEqual([expect.objectContaining({ path: "/auth/logout", method: "POST", body: undefined })]);
    expect(calls[0].headers.Authorization).toBe("Bearer ACC");
    expect(getToken()).toBe("ACC");
  });

  test("a 401 or no connection is just false: no renewal, no second request", async () => {
    await setToken("ACC", "driver", "account", "REF");
    server(reply(401, { detail: "Invalid or expired token" }));
    expect(await logout()).toBe(false);
    expect(calls.map((c) => c.path)).toEqual(["/auth/logout"]);
    expect(getToken()).toBe("ACC");
    server("offline");
    expect(await logout()).toBe(false);
  });

  test("nobody signed in: nothing to close, nothing sent", async () => {
    server(reply(200));
    expect(await logout()).toBe(true);
    expect(calls).toHaveLength(0);
  });
});
