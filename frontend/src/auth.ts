import { useEffect, useState } from "react";

import { storage } from "./utils/storage";

const KEY = "auth_token";
const ROLE_KEY = "auth_role";
const VIA_KEY = "auth_via"; // "code" | "device": whether this sign-in went through an access code
let token: string | null = null;
let role: "driver" | "tester" = "driver";
let via: "code" | "device" = "device";
let loaded = false;
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
  } else {
    await storage.secureRemove(KEY);
    await storage.secureRemove(ROLE_KEY);
    await storage.secureRemove(VIA_KEY);
  }
  emit();
}

export const authHeader = (): Record<string, string> => (token ? { Authorization: `Bearer ${token}` } : {});

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
