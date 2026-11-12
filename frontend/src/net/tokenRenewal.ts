/**
 * Keeps the token alive (a device's or an account session's), so a driver keeps their memory, history and profile past the access token's
 * 30 days (everything on the server hangs on the token's sub; a new sign-up is a new, empty driver).
 *
 * - With a refresh token: POST /api/auth/refresh trades it for a new pair.
 * - Without one (build 2 never stored it, or an older server sent none): POST /api/auth/renew trades the access
 *   token itself, valid or inside the server's grace window, for a pair. A server without /renew answers 404.
 *
 * `renewToken()` is single-flight: the 401s of a burst of requests share one attempt, and a refresh token is never
 * sent twice at once. An account's refresh token is single-use on the server: see `viaRefresh` and `attempt`.
 */
import { clearRefreshSent, getRefreshToken, getToken, isTester, markRefreshSent, refreshMaybeSpent, setToken, signedInVia, tokenHold } from "../auth";
import { BASE } from "../url";
import { tokenExpiry } from "../utils/jwt";

export { tokenExpiry };

/** "renewed": a new token is in place. "rejected": the server said this identity is gone (sign in again).
 *  "unavailable": no answer that settles it (offline, timeout, 5xx, 429): keep the token and try later. */
export type RenewOutcome = "renewed" | "rejected" | "unavailable";

/** Renew on start when the access token has less than this left. */
export const RENEW_BEFORE_MS = 7 * 86400_000;
const TIMEOUT_MS = 15000;

type Pair = { access_token?: unknown; refresh_token?: unknown; role?: unknown };

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
  // /auth/renew for an account session (a token with `sid`) sends a new access token only: the server keeps the
  // refresh token's hash, and the stored refresh token stays good. An answer without one keeps it.
  const rt = typeof data.refresh_token === "string" && data.refresh_token ? data.refresh_token : getRefreshToken();
  await setToken(data.access_token, role, signedInVia(), rt);
  return true;
}

/** Our API said "no" to this credential: a 401 with the JSON `detail` every 401 of ours carries. A 401, 403 or
 *  404 from a proxy or a captive portal is not a verdict about the driver and must never sign a phone out. */
export async function isOur401(res: Response): Promise<boolean> {
  if (res.status !== 401) return false;
  try {
    const body = (await (typeof res.clone === "function" ? res.clone() : res).json()) as { detail?: unknown };
    return typeof body?.detail === "string";
  } catch {
    return false;
  }
}

/** "dead": our API turned this credential down (the only answer that counts towards signing the phone out).
 *  "skip": this way is closed for another reason (no such endpoint, a proxy's 4xx): try the other one.
 *  "unavailable": no answer that settles anything (offline, a timeout, a 5xx, a page that is not ours). */
type Step = "renewed" | "unavailable" | "dead" | "skip";

async function judge(res: Response | null, from: string): Promise<Step> {
  if (!res) return "unavailable";
  if (res.ok) return (await adopt(res, from)) ? "renewed" : "unavailable";
  if (await isOur401(res)) return "dead";
  return res.status >= 400 && res.status < 500 && res.status !== 408 && res.status !== 429 ? "skip" : "unavailable";
}

/**
 * POST /auth/refresh. An account's refresh token is single-use: the server rotates it, and a spent one that shows
 * up again more than 10 s later closes the session (backend services/auth.py, REUSE_RACE_S). So the token is
 * marked as sent, on disk, before it goes out; the mark stays unless the server clearly did not take it. With the
 * mark up, `attempt` goes through the access token first, also after a restart.
 */
async function viaRefresh(from: string, rt: string): Promise<Step> {
  await markRefreshSent(rt);
  const out = await judge(await post("/auth/refresh", { body: JSON.stringify({ refresh_token: rt }) }), from);
  const current = getToken() === from && getRefreshToken() === rt;
  // Turned down (rotated elsewhere, the session closed, wiped): it never goes out again.
  if (out === "dead" && current) await setToken(from, isTester() ? "tester" : "driver", signedInVia(), null);
  // A 4xx that is not ours to read as "no": the request never got to the rotation.
  if (out === "skip" && current) await clearRefreshSent();
  return out;
}

/** POST /auth/renew with the access token itself (valid, or a device token inside the server's grace window):
 *  a whole new pair, whatever happened to the refresh token. */
const viaAccess = async (from: string): Promise<Step> => judge(await post("/auth/renew", { bearer: from }), from);

/** `accessRefused`: a request just got our 401 on this access token. */
async function attempt(accessRefused: boolean): Promise<RenewOutcome> {
  const hold = tokenHold();
  if (hold) await hold; // "Delete my data" is on the wire: renew whatever is left after it
  const from = getToken();
  if (!from) return "rejected";
  const rt = getRefreshToken();
  const account = signedInVia() === "account";
  const exp = tokenExpiry(from);
  // An account token gets no grace on the server: past its exp, or already answered 401, /auth/renew can only say no.
  const accessDead = account && (accessRefused || (exp !== null && exp <= Date.now()));
  const refresh = rt ? () => viaRefresh(from, rt) : null;
  const access = accessDead ? null : () => viaAccess(from);
  // An account renews through the access token while it is good (at the start-up check it always is: renewal
  // starts a week before expiry), so the single-use refresh token is spent only when nothing else is left. The
  // same when the refresh token may already be spent. A device or code token keeps the old order: its refresh
  // token is not single-use, and an expired one still renews inside the server's grace window.
  const accessFirst = account || refreshMaybeSpent();
  const steps = (accessFirst ? [access, refresh] : [refresh, access]).filter((f): f is () => Promise<Step> => f !== null);
  let verdicts = accessDead ? 1 : 0;
  for (const step of steps) {
    const out = await step();
    if (out === "renewed" || out === "unavailable") return out;
    if (out === "dead") verdicts++;
  }
  // Signed out only when our API said no to everything the phone holds. Nothing here registers a new anonymous
  // device; the driver signs in again (after401).
  const held = (rt ? 1 : 0) + 1;
  return verdicts >= held ? "rejected" : "unavailable";
}

// One attempt at a time, shared by every caller: two requests that get a 401 together, the start-up check and a
// request, a return to the foreground. Rotation on the server has exactly one winner per refresh token, so the
// same token must never go out twice.
let inflight: Promise<RenewOutcome> | null = null;

export function renewToken(accessRefused = false): Promise<RenewOutcome> {
  if (!inflight) {
    inflight = attempt(accessRefused)
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

/** Our 401 (isOur401) on a request that carried `sent` (still the current token): one renewal attempt. The phone is signed out
 *  (the sign-in screen, with the previous driver's data cleared) only when the server rejected the identity;
 *  offline or a server error keeps the token, so a dead zone never signs a driver out. Never a new anonymous
 *  device: an account's data is on the server, and whoever signs in next gets their own. True when a new token is in. */
export async function after401(sent: string): Promise<boolean> {
  const outcome = await renewToken(true);
  if (outcome === "rejected" && getToken() === sent) await rejected(sent);
  return outcome === "renewed";
}

/** Tests only. */
export function resetRenewalForTests() {
  inflight = null;
  rejecting = null;
}
