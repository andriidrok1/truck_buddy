import { useEffect, useState } from "react";

import { storage } from "./utils/storage";

const KEY = "auth_token";
const ROLE_KEY = "auth_role";
let token: string | null = null;
let role: "driver" | "tester" = "driver";
let loaded = false;
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((l) => l());

export const getToken = () => token;
export const isTester = () => role === "tester";

export async function loadToken(): Promise<string | null> {
  if (!loaded) {
    token = await storage.secureGet(KEY, null);
    role = (await storage.secureGet(ROLE_KEY, null)) === "tester" ? "tester" : "driver";
    loaded = true;
    emit();
  }
  return token;
}

export async function setToken(t: string | null, r: "driver" | "tester" = "driver") {
  token = t;
  role = t ? r : "driver";
  loaded = true;
  if (t) {
    await storage.secureSet(KEY, t);
    await storage.secureSet(ROLE_KEY, role);
  } else {
    await storage.secureRemove(KEY);
    await storage.secureRemove(ROLE_KEY);
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
