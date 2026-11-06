import { getLang } from "./i18n";
import { useEffect, useState } from "react";

import { settleWipe, storage } from "./utils/storage";

const KEY = "auth_token";
const ROLE_KEY = "auth_role";
const VIA_KEY = "auth_via"; // "code" | "device": whether this sign-in went through an access code
let token: string | null = null;
let role: "driver" | "tester" = "driver";
let via: "code" | "device" = "device";
let loaded = false;
// "Delete my data": while the server DELETE is on the wire, every other authenticated request waits for it to settle
// (api.ts awaits `tokenHold()`), so nothing reaches the server with the old token after the delete started and no 401
// signs the phone out behind the dialog. On failure the requests go out as before; on success after the wipe,
// without a token. Anything that reads the header directly (a satellite thumbnail) gets none meanwhile.
let hold: { promise: Promise<void>; release: () => void } | null = null;
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((l) => l());

// The driver asked to type an access code (sign-out, or "I have a code"): show the code screen
// instead of signing in automatically.
let codeWanted = false;
export const codeRequested = () => codeWanted;
export function requestCode(on = true) {
  codeWanted = on;
  emit();
}

export const getToken = () => token;
export const isTester = () => role === "tester";
/** True when the driver typed an access code; open sign-ups have nothing to "sign out" back to. */
export const signedInWithCode = () => via === "code";

export async function loadToken(): Promise<string | null> {
  if (!loaded) {
    token = await storage.secureGet(KEY, null);
    role = (await storage.secureGet(ROLE_KEY, null)) === "tester" ? "tester" : "driver";
    via = (await storage.secureGet(VIA_KEY, null)) === "code" ? "code" : "device";
    loaded = true;
    emit();
  }
  return token;
}

export async function setToken(t: string | null, r: "driver" | "tester" = "driver", v: "code" | "device" = "device") {
  token = t;
  role = t ? r : "driver";
  via = t ? v : "device";
  loaded = true;
  if (t) {
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
  }
  emit();
}

/** "Delete my data": no token, no code screen; once the gates pass again, DeviceGate signs the phone in as a new
 *  anonymous device (the server hands out a new identity for every POST /auth/device). */
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

/** "loading" until the stored token is read, then whether the driver has entered the access code. */
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
