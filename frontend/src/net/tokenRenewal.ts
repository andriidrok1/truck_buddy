/**
 * Keeps the device token alive, so a driver keeps their memory, history and profile past the access token's
 * 30 days (everything on the server hangs on the token's sub; a new sign-up is a new, empty driver).
 *
 * - With a refresh token: POST /api/auth/refresh trades it for a new pair.
 * - Without one (build 2 never stored it, or an older server sent none): POST /api/auth/renew trades the access
 *   token itself, valid or inside the server's grace window, for a pair. A server without /renew answers 404.
 *
 * `renewToken()` is single-flight: the 401s of a burst of requests share one attempt.
 */
import { getRefreshToken, getToken, isTester, setToken, signedInVia, tokenHold } from "../auth";
import { BASE } from "../url";

/** "renewed": a new token is in place. "rejected": the server said this identity is gone (sign in again).
 *  "unavailable": no answer that settles it (offline, timeout, 5xx, 429): keep the token and try later. */
export type RenewOutcome = "renewed" | "rejected" | "unavailable";

/** Renew on start when the access token has less than this left. */
export const RENEW_BEFORE_MS = 7 * 86400_000;
const TIMEOUT_MS = 15000;

type Pair = { access_token?: unknown; refresh_token?: unknown; role?: unknown };

/** The `exp` of a JWT in ms, or null when the token is not one we can read (no signature check: the server does it). */
export function tokenExpiry(t: string | null): number | null {
  const part = t?.split(".")[1];
  if (!part) return null;
  try {
    const b64 = part.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (part.length % 4)) % 4);
    const exp = (JSON.parse(decodeBase64(b64)) as { exp?: unknown }).exp;
    return typeof exp === "number" && Number.isFinite(exp) ? exp * 1000 : null;
  } catch {
    return null;
  }
}

const B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

/** atob without relying on the engine having it (the payload is ASCII JSON). */
function decodeBase64(s: string): string {
  let out = "";
  let bits = 0;
  let acc = 0;
  for (const ch of s) {
    if (ch === "=") break;
    const v = B64.indexOf(ch);
    if (v < 0) throw new Error("not base64");
    acc = (acc << 6) | v;
    bits += 6;
    if (bits >= 8) {
      bits -= 8;
      out += String.fromCharCode((acc >> bits) & 0xff);
    }
  }
  return out;
}

async function post(path: string, init: { body?: string; bearer?: string }): Promise<Response | null> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), TIMEOUT_MS);
  try {
    return await fetch(`${BASE}${path}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(init.bearer ? { Authorization: `Bearer ${init.bearer}` } : {}),
      },
      body: init.body,
      signal: ctrl.signal,
    });
  } catch {
    return null; // offline / timed out
  } finally {
    clearTimeout(timer);
  }
}

/** A 2xx with a usable pair: store it. Returns false when the body is not one. */
async function adopt(res: Response, from: string): Promise<boolean> {
  let data: Pair;
  try {
    data = (await res.json()) as Pair;
  } catch {
    return false;
  }
  if (typeof data?.access_token !== "string" || !data.access_token) return false;
  if (getToken() !== from) return true; // signed out or replaced meanwhile: leave the new state alone
  const role = data.role === "tester" || (data.role === undefined && isTester()) ? "tester" : "driver";
  const rt = typeof data.refresh_token === "string" && data.refresh_token ? data.refresh_token : null;
  await setToken(data.access_token, role, signedInVia(), rt);
  return true;
}

const settles = (status: number) => status === 401 || status === 403 || status === 404;

async function attempt(): Promise<RenewOutcome> {
  const hold = tokenHold();
  if (hold) await hold; // "Delete my data" is on the wire: renew whatever is left after it
  const from = getToken();
  if (!from) return "rejected";
  const rt = getRefreshToken();
  if (rt) {
    const res = await post("/auth/refresh", { body: JSON.stringify({ refresh_token: rt }) });
    if (!res) return "unavailable";
    if (res.ok) return (await adopt(res, from)) ? "renewed" : "unavailable";
    if (!settles(res.status) && res.status !== 422) return "unavailable";
    // the refresh token is gone (rotated elsewhere, wiped): the access token may still do
  }
  const res = await post("/auth/renew", { bearer: from });
  if (!res) return "unavailable";
  if (res.ok) return (await adopt(res, from)) ? "renewed" : "unavailable";
  if (settles(res.status)) return "rejected";
  return "unavailable";
}

let inflight: Promise<RenewOutcome> | null = null;

export function renewToken(): Promise<RenewOutcome> {
  if (!inflight) {
    inflight = attempt()
      .catch((): RenewOutcome => "unavailable")
      .finally(() => {
        inflight = null;
      });
  }
  return inflight;
}

/** On start: renew when the token is close to its expiry (or past it), or when no refresh token is stored yet.
 *  Never signs out: a "rejected" here is left for the next request's 401 to act on. */
export async function renewOnStart(now = Date.now()): Promise<RenewOutcome | null> {
  const t = getToken();
  if (!t) return null;
  const exp = tokenExpiry(t);
  const due = !getRefreshToken() || (exp !== null && exp - now < RENEW_BEFORE_MS);
  if (!due) return null;
  return renewToken();
}

/** A 401 on a request that carried `sent` (still the current token): one renewal attempt. The token is dropped
 *  (DeviceGate then signs the phone up again) only when the server rejected the identity; offline or a server
 *  error keeps it, so a dead zone never turns a driver into a new, empty device. True when a new token is in. */
export async function after401(sent: string): Promise<boolean> {
  const outcome = await renewToken();
  if (outcome === "rejected" && getToken() === sent) await setToken(null);
  return outcome === "renewed";
}
