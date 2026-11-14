// Sign in with Apple (expo-apple-authentication). The module is required lazily, inside a try: Android and jest
// have no native side for it, and a static import would run at startup on every platform.
//
// The nonce: a random string made here for this one sign-in. Apple gets its SHA-256 and puts that into the
// identity token; the server gets the raw string and checks the token carries its hash (backend
// services/identity.py), so a token lifted from another sign-in cannot be replayed.

import { Platform } from "react-native";

import { storage } from "../utils/storage";

export type AppleModule = typeof import("expo-apple-authentication");
type CryptoModule = typeof import("expo-crypto");

export type AppleCredential = {
  /** Apple's id for the person (stable for this app's team): ties a remembered name to the right Apple ID. */
  user: string;
  identityToken: string;
  rawNonce: string;
  /** For the server to revoke the sign-in when the account is deleted. */
  authorizationCode: string | null;
  /** Apple hands the name over on the very first authorization only. */
  name: string | null;
};

export type AppleResult = { type: "success"; credential: AppleCredential } | { type: "cancelled" };

let cached: AppleModule | null | undefined;

export function loadApple(): AppleModule | null {
  if (cached !== undefined) return cached;
  if (Platform.OS !== "ios") {
    cached = null;
    return cached;
  }
  try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const m = require("expo-apple-authentication") as Partial<AppleModule> | undefined;
    cached = m?.signInAsync && m?.isAvailableAsync && m?.AppleAuthenticationButton ? (m as AppleModule) : null;
  } catch (e) {
    console.log(`[signin] Apple sign-in not available: ${String(e)}`);
    cached = null;
  }
  return cached;
}

const hex = (bytes: Uint8Array) => Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");

/** The raw nonce (64 hex characters of randomness) and its SHA-256 as lowercase hex, which is what the server
 *  computes from the raw one. */
export async function makeNonce(): Promise<{ raw: string; hashed: string }> {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const Crypto = require("expo-crypto") as CryptoModule;
  const raw = hex(await Crypto.getRandomBytesAsync(32));
  const hashed = await Crypto.digestStringAsync(Crypto.CryptoDigestAlgorithm.SHA256, raw, { encoding: Crypto.CryptoEncoding.HEX });
  return { raw, hashed: hashed.toLowerCase() };
}

/** The driver closed Apple's sheet: not an error, nothing to show. */
const isCancel = (e: unknown) => (e as { code?: unknown } | null)?.code === "ERR_REQUEST_CANCELED";

export async function signInWithApple(): Promise<AppleResult> {
  const apple = loadApple();
  if (!apple) throw new Error("Sign in with Apple is not available on this device");
  const nonce = await makeNonce();
  let credential: Awaited<ReturnType<AppleModule["signInAsync"]>>;
  try {
    credential = await apple.signInAsync({
      requestedScopes: [apple.AppleAuthenticationScope.FULL_NAME, apple.AppleAuthenticationScope.EMAIL],
      nonce: nonce.hashed,
    });
  } catch (e) {
    if (isCancel(e)) return { type: "cancelled" };
    throw e;
  }
  if (!credential.identityToken) throw new Error("Apple returned no identity token");
  const name = [credential.fullName?.givenName, credential.fullName?.familyName].filter((p): p is string => !!p?.trim()).join(" ").trim();
  return {
    type: "success",
    credential: { user: credential.user, identityToken: credential.identityToken, rawNonce: nonce.raw, authorizationCode: credential.authorizationCode ?? null, name: name || null },
  };
}

// Apple hands the name over on the very first authorization only. If the server call after it fails (no signal),
// the next tap gets a credential without one, and the account would be made nameless. So the name is kept, in
// memory and on disk, for the same Apple ID until a sign-in succeeds.
const NAME_KEY = "apple_name_pending_v1";
let pendingName: { user: string; name: string } | null | undefined; // undefined = not read yet

async function readPendingName(): Promise<{ user: string; name: string } | null> {
  if (pendingName === undefined) {
    const raw = await storage.getItem<Record<string, unknown> | null>(NAME_KEY, null);
    const ok = raw !== null && typeof raw === "object" && typeof raw.user === "string" && typeof raw.name === "string" && !!raw.name;
    if (pendingName === undefined) pendingName = ok ? { user: raw.user as string, name: raw.name as string } : null;
  }
  return pendingName;
}

/** The credential with the name Apple gave on an earlier, failed try, when this one has none. A name that did
 *  arrive is remembered first. */
export async function withRememberedName(c: AppleCredential): Promise<AppleCredential> {
  if (c.name) {
    pendingName = { user: c.user, name: c.name };
    await storage.setItem(NAME_KEY, pendingName);
    return c;
  }
  const kept = await readPendingName();
  return kept && kept.user === c.user ? { ...c, name: kept.name } : c;
}

/** The server has the account (and the name): nothing to remember. */
export async function forgetRememberedName(): Promise<void> {
  pendingName = null;
  await storage.removeItem(NAME_KEY);
}

/** Tests only. */
export function resetAppleForTests() {
  cached = undefined;
  pendingName = undefined;
}
