import { getLang } from "./i18n";
import { useEffect, useState } from "react";

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
let token: string | null = null;
let refresh: string | null = null;
let role: "driver" | "tester" = "driver";
let via: SignedInVia = "device";
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
export const isTester = () => role === "tester";
export const signedInVia = () => via;
/** True when the driver typed an access code. */
export const signedInWithCode = () => via === "code";
/** True when the driver signed in with Apple or Google: the data belongs to an account, not to this phone. */
export const signedInWithAccount = () => via === "account";

export async function loadToken(): Promise<string | null> {
  if (!loaded) {
    token = await storage.secureGet(KEY, null);
    role = (await storage.secureGet(ROLE_KEY, null)) === "tester" ? "tester" : "driver";
    const v = await storage.secureGet(VIA_KEY, null);
    via = v === "code" || v === "account" ? v : "device";
    refresh = token ? await storage.secureGet(REFRESH_KEY, null) : null;
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
  refresh = t ? rt : null;
  loaded = true;
  if (t) {
    await storage.secureSet(KEY, t);
    await storage.secureSet(ROLE_KEY, role);
    await storage.secureSet(VIA_KEY, via);
    if (refresh) await storage.secureSet(REFRESH_KEY, refresh);
    else await storage.secureRemove(REFRESH_KEY);
    // A "Delete my data" that could not remove everything: the old identity's leftovers go now, not the new
    // identity on the next cold start (pendingWipe.ts).
    await settleWipe().catch(() => []);
  } else {
    await storage.secureRemove(KEY);
    await storage.secureRemove(ROLE_KEY);
    await storage.secureRemove(VIA_KEY);
    await storage.secureRemove(REFRESH_KEY);
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
