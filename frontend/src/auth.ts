import { useEffect, useState } from "react";

import { storage } from "./utils/storage";

const KEY = "auth_token";
let token: string | null = null;
let loaded = false;
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((l) => l());

export const getToken = () => token;

export async function loadToken(): Promise<string | null> {
  if (!loaded) {
    token = await storage.secureGet(KEY, null);
    loaded = true;
    emit();
  }
  return token;
}

export async function setToken(t: string | null) {
  token = t;
  loaded = true;
  if (t) await storage.secureSet(KEY, t);
  else await storage.secureRemove(KEY);
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
