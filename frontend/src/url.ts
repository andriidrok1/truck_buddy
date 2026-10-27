// Backend URL helpers. Kept free of React Native imports so they can be unit-tested in plain jest;
// src/api.ts re-exports them, so callers keep importing from "@/src/api".

const ORIGIN = process.env.EXPO_PUBLIC_BACKEND_URL;
if (!ORIGIN) {
  // Fail loudly at load time: without it every request would go to "undefined/api" and look like a dead connection.
  throw new Error("EXPO_PUBLIC_BACKEND_URL is not set (eas.json build env, or frontend/.env for local runs)");
}

/** `${origin}/api`: every REST call is built on top of this. */
export const BASE = `${ORIGIN}/api`;

export const absoluteUrl = (path: string) => `${ORIGIN}${path}`;

export const apiUrl = (path: string) => `${BASE}${path}`;
