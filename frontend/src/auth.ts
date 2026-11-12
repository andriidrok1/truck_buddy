import { getLang } from "./i18n";
import { useEffect, useState } from "react";

import { tokenHasSid, tokenSub } from "./utils/jwt";
import { settleWipe, storage } from "./utils/storage";

const KEY = "auth_token";
const ROLE_KEY = "auth_role";
// How this phone signed in: "account" (Apple or Google, the token carries a session), "code" (an access code:
// testers, the emulator stand) or "device" (the anonymous sign-up of build 2).
const VIA_KEY = "auth_via";
export type SignedInVia = "code" | "device" | "account";
// The refresh token the server hands out with the access token (POST /api/auth/refresh trades it for a new pair).
// Build 2 never kept it; net/tokenRenewal.ts gets one with POST /api/auth/renew on the first start after an update.
const REFRESH_KEY = "auth_refresh";
// The refresh token that has been sent to POST /api/auth/refresh and not replaced since. Written BEFORE the request
// goes out and kept across restarts: if the answer was lost (a dead zone, a kill, a 502 from the edge) the server
// may have spent the token, and a spent token that shows up again closes an account's session. While this equals
// the stored refresh token, net/tokenRenewal.ts goes through the access token first.
const REFRESH_SENT_KEY = "auth_refresh_sent";
let token: string | null = null;
let refresh: string | null = null;
let refreshSent: string | null = null;
let sub: string | null = null;
let role: "driver" | "tester" = "driver";
let via: SignedInVia = "device";
// False when the Keychain could not be read for `auth_via` (before the first unlock after a reboot): how this
// phone signed in is then a guess from the token, and nothing may be decided on it (firstLaunch.ts).
let viaKnown = true;
let loaded = false;
// "Delete my data": while the server DELETE is on the wire, every other authenticated request waits for it to settle
// (api.ts awaits `tokenHold()`), so nothing reaches the server with the old token after the delete started and no 401
// signs the phone out behind the dialog. On failure the requests go out as before; on success after the wipe,
// without a token. Anything that reads the header directly (a satellite thumbnail) gets none meanwhile.
let hold: { promise: Promise<void>; release: () => void } | null = null;
// Moves on every time the phone stops being someone (sign-out, a rejected token, "Delete my data"). A request that
// comes back with a 401 after that belongs to the driver who left: it must not be sent again with the token of
// whoever signed in since (src/api.ts request).
let epoch = 0;
export const identityEpoch = () => epoch;
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((l) => l());

// The driver asked to type an access code (a tester signing out): the sign-in screen opens on the code screen, in
// the builds that have one (src/components/SignInGate.tsx).
let codeWanted = false;
export const codeRequested = () => codeWanted;
export function requestCode(on = true) {
  codeWanted = on;
  emit();
}

export const getToken = () => token;
export const getRefreshToken = () => refresh;
/** Whose data this phone is working with: the token's `sub` (null when signed out). */
export const identitySub = () => sub;
/** True when the stored refresh token has already been sent once and may be spent on the server. */
export const refreshMaybeSpent = () => refresh !== null && refreshSent === refresh;

/** Before a refresh token goes out. Awaited: the mark must be on disk before the request is. */
export async function markRefreshSent(rt: string): Promise<void> {
  refreshSent = rt;
  await storage.secureSet(REFRESH_SENT_KEY, rt);
}

/** The server answered that it did not take the token (it never looked at it): it is as good as unsent. */
export async function clearRefreshSent(): Promise<void> {
  refreshSent = null;
  await storage.secureRemove(REFRESH_SENT_KEY);
}
export const isTester = () => role === "tester";
export const signedInVia = () => via;
export const signedInViaKnown = () => viaKnown;

/** What `auth_via` says, or a guess from the token when the key is absent (an account token carries `sid`). */
async function readVia(): Promise<void> {
  const r = await storage.secureRead<string>(VIA_KEY);
  if (r.ok && (r.value === "code" || r.value === "account" || r.value === "device")) {
    via = r.value;
    viaKnown = true;
    return;
  }
  via = tokenHasSid(token) ? "account" : "device";
  viaKnown = r.ok; // absent: the token decides; unreadable: a guess only
}

/** The next foreground after a failed read: try again. True when the answer changed. */
export async function rereadVia(): Promise<boolean> {
  if (viaKnown || !token) return false;
  const before = via;
  await readVia();
  if (viaKnown || via !== before) emit();
  return viaKnown;
}
/** True when the driver typed an access code. */
export const signedInWithCode = () => via === "code";
/** True when the driver signed in with Apple or Google: the data belongs to an account, not to this phone. */
export const signedInWithAccount = () => via === "account";

export async function loadToken(): Promise<string | null> {
  if (!loaded) {
    token = await storage.secureGet(KEY, null);
    role = (await storage.secureGet(ROLE_KEY, null)) === "tester" ? "tester" : "driver";
    await readVia();
    refresh = token ? await storage.secureGet(REFRESH_KEY, null) : null;
    refreshSent = refresh ? await storage.secureGet(REFRESH_SENT_KEY, null) : null;
    sub = tokenSub(token);
    loaded = true;
    emit();
  }
  return token;
}

/** `rt`: the refresh token that came with `t` (none: whatever was stored goes, it belonged to the previous token). */
export async function setToken(
  t: string | null,
  r: "driver" | "tester" = "driver",
  v: SignedInVia = "device",
  rt: string | null = null,
) {
  if (!t && token) epoch++;
  token = t;
  role = t ? r : "driver";
  via = t ? v : "device";
  viaKnown = true;
  refresh = t ? rt : null;
  sub = tokenSub(t);
  const staleMark = refreshSent !== null && refreshSent !== refresh;
  if (staleMark) refreshSent = null;
  loaded = true;
  if (t) {
    // The refresh token first, then the access token: a kill in between leaves the new refresh token with the old,
    // still valid access token, and either renews the other. The other order left a new access token with a
    // refresh token the server had already spent. Separate Keychain items on purpose: one combined value would
    // need a migration of what 1.0 stored, and this order makes the gap harmless.
    if (refresh) await storage.secureSet(REFRESH_KEY, refresh);
    else await storage.secureRemove(REFRESH_KEY);
    if (staleMark) await storage.secureRemove(REFRESH_SENT_KEY);
    await storage.secureSet(KEY, t);
    await storage.secureSet(ROLE_KEY, role);
    await storage.secureSet(VIA_KEY, via);
    // A "Delete my data" that could not remove everything: the old identity's leftovers go now, not the new
    // identity on the next cold start (pendingWipe.ts).
    await settleWipe().catch(() => []);
  } else {
    await storage.secureRemove(KEY);
    await storage.secureRemove(ROLE_KEY);
    await storage.secureRemove(VIA_KEY);
    await storage.secureRemove(REFRESH_KEY);
    await storage.secureRemove(REFRESH_SENT_KEY);
  }
  emit();
}

/** "Delete my data", sign-out, or the server turned the token down: no token, no code screen. The root layout
 *  shows the sign-in screen (after the language and the intro, on a full wipe). */
export async function forgetIdentity() {
  codeWanted = false;
  await setToken(null);
  hold?.release(); // the queued requests go now, without the token
}

// Every authenticated request also says the driver's language (backend services/i18n.LangMiddleware).
/** Holds every authenticated request from now on; the returned function lets them go (idempotent). */
export function holdToken(): () => void {
  if (hold) return hold.release;
  let resolve: () => void = () => {};
  const promise = new Promise<void>((r) => {
    resolve = r;
  });
  const h = {
    promise,
    release: () => {
      if (hold === h) hold = null;
      resolve();
    },
  };
  hold = h;
  return h.release;
}

/** What a request waits on before it reads the token; null when nothing is held. */
export const tokenHold = (): Promise<void> | null => hold?.promise ?? null;

export const authHeader = (): Record<string, string> => ({ ...(token && !hold ? { Authorization: `Bearer ${token}` } : {}), "X-Lang": getLang() });

/** "loading" until the stored token is read, then whether the phone is signed in. */
export function useAuthState(): "loading" | "in" | "out" {
  const [, force] = useState(0);
  useEffect(() => {
    const l = () => force((n) => n + 1);
    listeners.add(l);
    loadToken();
    return () => {
      listeners.delete(l);
    };
  }, []);
  if (!loaded) return "loading";
  return token ? "in" : "out";
}
