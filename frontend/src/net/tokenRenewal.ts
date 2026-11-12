/**
 * Keeps the token alive (a device's or an account session's), so a driver keeps their memory, history and profile past the access token's
 * 30 days (everything on the server hangs on the token's sub; a new sign-up is a new, empty driver).
 *
 * - With a refresh token: POST /api/auth/refresh trades it for a new pair.
 * - Without one (build 2 never stored it, or an older server sent none): POST /api/auth/renew trades the access
 *   token itself, valid or inside the server's grace window, for a pair. A server without /renew answers 404.
 *
 * `renewToken()` is single-flight: the 401s of a burst of requests share one attempt, and a refresh token is never
 * sent twice at once. An account's refresh token is single-use on the server: see `unanswered` below.
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

type Step = "renewed" | "unavailable" | "dead";

/** A refresh token whose trade got no answer (a dead zone, a timeout). The request may have reached the server,
 *  and then the token is spent: an account session treats a spent token that shows up again later as stolen and
 *  closes (backend services/auth.py, REUSE_RACE_S). So it is not sent a second time while the access token can
 *  still do the job. In memory only: after a restart the refresh token is tried as usual. */
let unanswered: string | null = null;

/** POST /auth/refresh. "dead": the server will never take this refresh token. */
async function viaRefresh(from: string, rt: string): Promise<Step> {
  const res = await post("/auth/refresh", { body: JSON.stringify({ refresh_token: rt }) });
  if (!res) {
    unanswered = rt;
    return "unavailable";
  }
  if (res.ok) return (await adopt(res, from)) ? "renewed" : "unavailable";
  if (!settles(res.status) && res.status !== 422) return "unavailable";
  // Turned down (rotated elsewhere, the session closed, wiped): it never goes out again.
  if (res.status === 401 && getToken() === from && getRefreshToken() === rt) await setToken(from, isTester() ? "tester" : "driver", signedInVia(), null);
  return "dead";
}

/** POST /auth/renew with the access token itself (valid, or a device token inside the server's grace window). */
async function viaAccess(from: string): Promise<Step> {
  const res = await post("/auth/renew", { bearer: from });
  if (!res) return "unavailable";
  if (res.ok) return (await adopt(res, from)) ? "renewed" : "unavailable";
  return settles(res.status) ? "dead" : "unavailable";
}

async function attempt(): Promise<RenewOutcome> {
  const hold = tokenHold();
  if (hold) await hold; // "Delete my data" is on the wire: renew whatever is left after it
  const from = getToken();
  if (!from) return "rejected";
  const rt = getRefreshToken();
  const refresh = rt ? () => viaRefresh(from, rt) : null;
  const access = () => viaAccess(from);
  // The usual order: the refresh token, then the access token (the refresh token is gone, the access token may
  // still do). A refresh token that may already be spent goes last.
  const steps = !refresh ? [access] : rt === unanswered ? [access, refresh] : [refresh, access];
  for (const step of steps) {
    const out = await step();
    if (out !== "dead") return out;
  }
  // Both turned down: the identity is gone. Nothing here signs the phone up as a new anonymous device; the
  // driver signs in again (after401).
  return "rejected";
}

// One attempt at a time, shared by every caller: two requests that get a 401 together, the start-up check and a
// request, a return to the foreground. Rotation on the server has exactly one winner per refresh token, so the
// same token must never go out twice.
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

let onRejected: (() => Promise<unknown>) | null = null;
/** What to do when the server turns the identity down for good: src/state/firstLaunch.ts registers the sign-out
 *  that clears the previous driver's data and shows the sign-in screen. Without a handler the token just goes. */
export function setIdentityRejectedHandler(handler: (() => Promise<unknown>) | null) {
  onRejected = handler;
}

let rejecting: Promise<void> | null = null;

/** The identity is gone: one sign-out, however many requests found out at once. */
function rejected(sent: string): Promise<void> {
  if (!rejecting) {
    rejecting = (async () => {
      try {
        if (onRejected) await onRejected();
      } catch {
        // the token goes below whatever happened
      }
      if (getToken() === sent) await setToken(null);
    })().finally(() => {
      rejecting = null;
    });
  }
  return rejecting;
}

/** A 401 on a request that carried `sent` (still the current token): one renewal attempt. The phone is signed out
 *  (the sign-in screen, with the previous driver's data cleared) only when the server rejected the identity;
 *  offline or a server error keeps the token, so a dead zone never signs a driver out. Never a new anonymous
 *  device: an account's data is on the server, and whoever signs in next gets their own. True when a new token is in. */
export async function after401(sent: string): Promise<boolean> {
  const outcome = await renewToken();
  if (outcome === "rejected" && getToken() === sent) await rejected(sent);
  return outcome === "renewed";
}

/** Tests only. */
export function resetRenewalForTests() {
  unanswered = null;
  inflight = null;
  rejecting = null;
}
