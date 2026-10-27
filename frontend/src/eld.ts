// Motive (ELD) API key: lives in the phone's secure storage, travels only as the X-Motive-Key
// header on copilot requests so the backend can read hours of service. Never stored server-side.
import { useEffect, useState } from "react";

import { storage } from "./utils/storage";

const KEY = "eld_token_v1";
export const ELD_HEADER = "X-Motive-Key";

let token: string | null = null;
let loaded = false;
const listeners = new Set<(t: string | null) => void>();
const emit = () => listeners.forEach((l) => l(token));

/** Reads the stored key once; later calls are no-ops. Call where the auth token is loaded. */
export async function loadEldToken(): Promise<string | null> {
  if (!loaded) {
    const raw = await storage.secureGet<string | null>(KEY, null);
    token = typeof raw === "string" && raw.trim() ? raw.trim() : null;
    loaded = true;
    emit();
  }
  return token;
}

export const getEldToken = () => token;

/** Saves a key (trimmed) or removes it when empty / null. */
export async function setEldToken(t: string | null) {
  const next = t?.trim() || null;
  token = next;
  loaded = true;
  if (next) await storage.secureSet(KEY, next);
  else await storage.secureRemove(KEY);
  emit();
}

/** `{ "X-Motive-Key": key }` when a key is stored, `{}` otherwise. Spread into copilot request headers only. */
export const eldHeader = (): Record<string, string> => (token ? { [ELD_HEADER]: token } : {});

/** The stored key, or null; loads it on first use. */
export function useEldToken(): string | null {
  const [value, setValue] = useState(token);
  useEffect(() => {
    listeners.add(setValue);
    loadEldToken();
    return () => {
      listeners.delete(setValue);
    };
  }, []);
  return value;
}
