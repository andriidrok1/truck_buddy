// Which sign-in buttons this build can show (src/components/SignInGate.tsx).
//
// Apple: iOS only (no native Sign in with Apple on Android), and not in Expo Go: there the identity token is
// issued for Expo Go's bundle id, which the server does not accept. Google: iOS and Android builds; Expo Go has
// no native module for it. Whether Google is set up (client ids baked into the bundle) is a separate answer, so
// a build made without them shows the button and says why it does nothing instead of crashing.

import { isRunningInExpoGo } from "expo";
import { Linking, Platform } from "react-native";

import { loadApple } from "./apple";

/** Where the app runs. A parameter everywhere it matters, so tests say "Expo Go" or "a store build" outright. */
export type SignInEnv = { platform: string; expoGo: boolean; dev: boolean };

export const signInEnv = (): SignInEnv => ({ platform: Platform.OS, expoGo: isRunningInExpoGo(), dev: __DEV__ });

export type GoogleConfig = { webClientId: string; iosClientId: string };

/** OAuth client ids from the Google Cloud console; not secrets. Read at call time (tests change them). */
export const googleConfig = (): GoogleConfig => ({
  webClientId: process.env.EXPO_PUBLIC_GOOGLE_WEB_CLIENT_ID?.trim() ?? "",
  iosClientId: process.env.EXPO_PUBLIC_GOOGLE_IOS_CLIENT_ID?.trim() ?? "",
});

/** The web client id is what the id token is issued for on both platforms; iOS also needs its own client id. */
export function googleConfigured(platform: string, config: GoogleConfig = googleConfig()): boolean {
  if (!config.webClientId) return false;
  return platform !== "ios" || !!config.iosClientId;
}

const CLIENT_SUFFIX = ".apps.googleusercontent.com";

/** "123-abc.apps.googleusercontent.com" -> "com.googleusercontent.apps.123-abc": the URL scheme Google's iOS SDK
 *  returns through. Empty when the id is not an OAuth client id. The same rule as app.config.ts. */
export function reversedClientId(iosClientId: string): string {
  return iosClientId.endsWith(CLIENT_SUFFIX) && iosClientId.length > CLIENT_SUFFIX.length ? `com.googleusercontent.apps.${iosClientId.slice(0, -CLIENT_SUFFIX.length)}` : "";
}

/**
 * Whether tapping the Google button can work in THIS binary. On iOS the client ids in the bundle are not enough:
 * the binary must have been built with the URL scheme in its Info.plist, or Google's SDK raises an NSException
 * when the sheet opens (a bundle delivered over the air to a binary built without the variables). The app can
 * ask the system: app.config.ts lists the scheme under LSApplicationQueriesSchemes exactly when it registers it,
 * so `canOpenURL` answers true only in a binary that has both.
 */
export async function googleUsable(platform: string, config: GoogleConfig = googleConfig(), canOpen: (url: string) => Promise<boolean> = Linking.canOpenURL): Promise<boolean> {
  if (!googleConfigured(platform, config)) return false;
  if (platform !== "ios") return true;
  const scheme = reversedClientId(config.iosClientId);
  if (!scheme) return false;
  try {
    return (await canOpen(`${scheme}://`)) === true;
  } catch {
    return false;
  }
}

export type Providers = {
  apple: boolean;
  /** The Google button is shown. */
  google: boolean;
  /** ...and can work: the client ids are in the bundle. */
  googleConfigured: boolean;
};

export async function availableProviders(env: SignInEnv = signInEnv()): Promise<Providers> {
  const native = !env.expoGo && (env.platform === "ios" || env.platform === "android");
  let apple = false;
  if (native && env.platform === "ios") {
    try {
      apple = (await loadApple()?.isAvailableAsync()) === true;
    } catch {
      apple = false;
    }
  }
  return { apple, google: native, googleConfigured: native && (await googleUsable(env.platform)) };
}

/** "I have an access code" on the sign-in screen is visible at once in Expo Go and dev builds (the emulator stand
 *  signs in with a code there). A build a driver gets shows it only after a 3 s press on the avatar (App Review,
 *  support). */
export const codeLinkAllowed = (env: SignInEnv = signInEnv()): boolean => env.expoGo || env.dev;
