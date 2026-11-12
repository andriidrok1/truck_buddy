// Sign in with Google (@react-native-google-signin/google-signin 17, the "original" sign-in). Required lazily,
// inside a try: Expo Go and jest have no native module, and the library throws on import without one.
//
// The id token goes to the server, which checks it against Google's keys (backend services/identity.py). It is
// issued for the web client id (`webClientId`) on both platforms; iOS also needs its own client id, and the app
// returns from Google's sheet through the URL scheme set in app.config.ts.

import { Platform } from "react-native";

import { googleConfig, googleConfigured } from "./available";

export type GoogleModule = typeof import("@react-native-google-signin/google-signin");

export type GoogleResult =
  | { type: "success"; idToken: string }
  | { type: "cancelled" }
  /** The build has no Google client ids (or no native module): the button says so. */
  | { type: "not_configured" };

let cached: GoogleModule | null | undefined;
let configuredFor: string | null = null;

export function loadGoogle(): GoogleModule | null {
  if (cached !== undefined) return cached;
  if (Platform.OS === "web") {
    cached = null;
    return cached;
  }
  try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const m = require("@react-native-google-signin/google-signin") as Partial<GoogleModule> | undefined;
    cached = m?.GoogleSignin && m.statusCodes ? (m as GoogleModule) : null;
  } catch (e) {
    console.log(`[signin] Google sign-in not available: ${String(e)}`);
    cached = null;
  }
  return cached;
}

const codeOf = (e: unknown): string | null => {
  const code = (e as { code?: unknown } | null)?.code;
  return typeof code === "string" ? code : null;
};

export async function signInWithGoogle(): Promise<GoogleResult> {
  const config = googleConfig();
  if (!googleConfigured(Platform.OS, config)) return { type: "not_configured" };
  const google = loadGoogle();
  if (!google) return { type: "not_configured" };
  const { GoogleSignin, statusCodes } = google;

  // Once per set of ids (they never change in a build; tests do change them).
  const key = `${config.webClientId}|${config.iosClientId}`;
  if (configuredFor !== key) {
    GoogleSignin.configure({ webClientId: config.webClientId, ...(config.iosClientId ? { iosClientId: config.iosClientId } : {}) });
    configuredFor = key;
  }

  try {
    // Android: offers to update Google Play services when they are too old; throws when there are none.
    if (Platform.OS === "android") await GoogleSignin.hasPlayServices({ showPlayServicesUpdateDialog: true });
    const res = await GoogleSignin.signIn();
    if (res.type !== "success") return { type: "cancelled" };
    const idToken = res.data.idToken ?? (await GoogleSignin.getTokens()).idToken;
    if (!idToken) throw new Error("Google returned no id token");
    // Google's SDK remembers the account and would pick it silently next time; after a sign-out in the app the
    // driver must get the account chooser again. Our own session is what keeps the phone signed in.
    GoogleSignin.signOut().catch(() => {});
    return { type: "success", idToken };
  } catch (e) {
    const code = codeOf(e);
    // The sheet was closed, or a second tap landed while it was up.
    if (code !== null && (code === statusCodes.SIGN_IN_CANCELLED || code === statusCodes.IN_PROGRESS)) return { type: "cancelled" };
    throw e;
  }
}

/** Tests only. */
export function resetGoogleForTests() {
  cached = undefined;
  configuredFor = null;
}
