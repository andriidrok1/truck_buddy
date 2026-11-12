// Which sign-in buttons this build can show (src/components/SignInGate.tsx).
//
// Apple: iOS only (no native Sign in with Apple on Android), and not in Expo Go: there the identity token is
// issued for Expo Go's bundle id, which the server does not accept. Google: iOS and Android builds; Expo Go has
// no native module for it. Whether Google is set up (client ids baked into the bundle) is a separate answer, so
// a build made without them shows the button and says why it does nothing instead of crashing.

import { isRunningInExpoGo } from "expo";
import { Platform } from "react-native";

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
  return { apple, google: native, googleConfigured: native && googleConfigured(env.platform) };
}

/** "I have an access code" on the sign-in screen: Expo Go and dev builds only (the emulator stand signs in with a
 *  code there). Never in a build a driver gets. */
export const codeLinkAllowed = (env: SignInEnv = signInEnv()): boolean => env.expoGo || env.dev;
