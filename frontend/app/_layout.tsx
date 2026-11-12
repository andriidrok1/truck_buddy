import * as Sentry from "@sentry/react-native";
import { QueryClientProvider } from "@tanstack/react-query";
import { useFonts } from "expo-font";
import { Stack, usePathname, useRouter } from "expo-router";
import { StatusBar } from "expo-status-bar";
import * as SystemUI from "expo-system-ui";
import { useEffect, useRef, useState } from "react";
import { AppState, LogBox, StyleSheet } from "react-native";
import { GestureHandlerRootView } from "react-native-gesture-handler";
import { ReducedMotionConfig, ReduceMotion } from "react-native-reanimated";
import { KeyboardProvider } from "@/src/keyboard";

import { signedInVia, useAuthState } from "@/src/auth";
import { renewOnStart } from "@/src/net/tokenRenewal";
import { loadThemePref, useTheme } from "@/src/theme";
import { ConsentGate } from "@/src/components/ConsentGate";
import { LanguageGate } from "@/src/components/LanguageGate";
import { useLangStore } from "@/src/i18n";
import { startLocation } from "@/src/geo/location";
import "@/src/geo/driveTracking"; // defines the background location task at startup (the OS may wake the app for it)
import { ensureTruckProfile } from "@/src/hooks/useTruckProfile";
import { ensureSettings } from "@/src/state/settingsStore";
import { migrateStorage } from "@/src/utils/storageMigrations";
import { SignInGate } from "@/src/components/SignInGate";
import { WelcomeBack } from "@/src/components/WelcomeBack";
import { ErrorBoundary } from "@/src/components/error-boundary";
import { useConsent } from "@/src/consent";
import { effectiveAuth, retireDeviceIdentity, rootScreen } from "@/src/state/firstLaunch";
import { useIntroSeen } from "@/src/state/intro";
import { IntroGate } from "@/src/components/intro/IntroGate";
import { loadEldToken } from "@/src/eld";
import { useProfileSync, useRestorePending } from "@/src/data/profile";
import { useWelcomeBack } from "@/src/state/account";
import { queryClient } from "@/src/query-client";
import { setContractWarningSink } from "@/src/api/schemas";

// Crash and error reporting. No DSN (local dev, Expo Go) = Sentry stays dormant and every call is a no-op.
const SENTRY_DSN = process.env.EXPO_PUBLIC_SENTRY_DSN?.trim();
Sentry.init({
  dsn: SENTRY_DSN || undefined,
  enabled: !!SENTRY_DSN,
  tracesSampleRate: 0.1,
  environment: process.env.EXPO_PUBLIC_SENTRY_ENV ?? (__DEV__ ? "development" : "production"),
  sendDefaultPii: false,
});

// A server answer that no longer fits the shape the app reads: the app carries on with the raw object, the
// mismatch lands in Sentry (dev: console).
setContractWarningSink((path, issues) => {
  if (__DEV__) console.warn(`[contract] ${path}: ${issues.join("; ")}`);
  else Sentry.captureMessage(`contract ${path}: ${issues.join("; ")}`, "warning");
});

// Dev only: the red/yellow boxes over the map get in the way of driving the stand. Production builds
// keep them (they do not render there anyway), and errors go to Sentry.
if (__DEV__) LogBox.ignoreAllLogs(true);

function RootLayout() {
  const { scheme, colors } = useTheme();
  // The window behind the (translucent) status bar: white by default, a bright band above every dark screen
  // in dark mode (QA 2026-10-31, Н24). Expo Go keeps its own opaque bar, so this shows in builds.
  useEffect(() => {
    SystemUI.setBackgroundColorAsync(colors.surface).catch(() => {});
  }, [colors.surface]);
  // The stored theme choice must be in before the first screen, or a dark user sees a white flash.
  const [themeReady, setThemeReady] = useState(false);
  useEffect(() => {
    loadThemePref().finally(() => setThemeReady(true));
  }, []);
  const stored = useAuthState();
  // Sign-in is required: the anonymous device token of build 1.0 does not count (src/state/firstLaunch.ts). The
  // root goes to the sign-in screen at once, and the token and what the phone kept under it are cleared.
  const auth = effectiveAuth(stored, signedInVia());
  useEffect(() => {
    if (stored === "in") retireDeviceIdentity().catch(() => {});
  }, [stored]);
  // Keep the token alive (30-day access token): on start and on every return to the foreground, renew when it
  // is close to expiry or no refresh token is stored yet. Offline it just tries again next time.
  useEffect(() => {
    if (auth !== "in") return;
    renewOnStart().catch(() => {});
    const sub = AppState.addEventListener("change", (s) => {
      if (s === "active") renewOnStart().catch(() => {});
    });
    return () => sub.remove();
  }, [auth]);
  const consent = useConsent();
  const langLoaded = useLangStore((s) => s.loaded);
  const langChosen = useLangStore((s) => s.chosen);
  // Read with the rest of the startup state: the consent screen must not flash before the intro on a fresh install.
  const introSeen = useIntroSeen();
  // The Motive key is read alongside the auth token so the first copilot request already carries it.
  useEffect(() => {
    loadEldToken();
  }, []);
  // The truck profile too: every request awaits it, but reading it here means it is in before the map
  // screen even mounts, so nothing is ever planned for the 13'6" default on a taller rig.
  // Storage first: the schema migration runs before any store reads its document, then the profile and the
  // settings load (the stores answer with defaults until then, which is what a fresh install sees anyway).
  const [storageReady, setStorageReady] = useState(false);
  useEffect(() => {
    migrateStorage()
      .then(() => Promise.all([ensureTruckProfile(), ensureSettings()]))
      .finally(() => setStorageReady(true));
  }, []);
  // The server copy of the truck (a fleet sets it there; a returning driver's is only there) once the local one is
  // up and the phone is signed in: on a cold start with a token, and right after a sign-in. Offline, the local stands.
  useProfileSync(storageReady && auth === "in");
  // A returning driver who just signed in sees "Welcome back" once, after consent; it stays up (also across a
  // restart) until the server's profile has landed, so nothing is saved over it from the map.
  const restoring = useRestorePending();
  const welcomeBack = useWelcomeBack() || restoring === true;
  // GPS from the moment the driver is signed in and consent is in (the consent screen starts it too): the first
  // fix is ready before the map screen mounts instead of 7-9 s after it. Not on the sign-in screen after a sign-out.
  useEffect(() => {
    if (consent === "yes" && auth === "in") startLocation({ ask: false });
  }, [consent, auth]);
  // Pre-load the icon font (prevents blank icons in Expo Go) and app fonts.
  const [loaded, error] = useFonts({
    MaterialDesignIcons: require("@react-native-vector-icons/material-design-icons/fonts/MaterialDesignIcons.ttf"),
    "PlusJakartaSans-Regular": require("../assets/fonts/PlusJakartaSans-Regular.ttf"),
    "PlusJakartaSans-Medium": require("../assets/fonts/PlusJakartaSans-Medium.ttf"),
    "PlusJakartaSans-SemiBold": require("../assets/fonts/PlusJakartaSans-SemiBold.ttf"),
    "PlusJakartaSans-Bold": require("../assets/fonts/PlusJakartaSans-Bold.ttf"),
    "PlusJakartaSans-ExtraBold": require("../assets/fonts/PlusJakartaSans-ExtraBold.ttf"),
    // Plus Jakarta Sans has no Cyrillic: Russian text is drawn in Manrope (OFL, assets/fonts/Manrope-OFL.txt),
    // the same weights, picked by src/i18n/Text.tsx.
    "Manrope-Regular": require("../assets/fonts/Manrope-Regular.ttf"),
    "Manrope-Medium": require("../assets/fonts/Manrope-Medium.ttf"),
    "Manrope-SemiBold": require("../assets/fonts/Manrope-SemiBold.ttf"),
    "Manrope-Bold": require("../assets/fonts/Manrope-Bold.ttf"),
    "Manrope-ExtraBold": require("../assets/fonts/Manrope-ExtraBold.ttf"),
  });

  const screen = rootScreen(langChosen, introSeen === true, consent, auth, welcomeBack);
  const ready = !((!loaded && !error) || auth === "loading" || consent === "loading" || !themeReady || !storageReady || !langLoaded || introSeen === undefined || restoring === undefined);
  // The stack remembers where it was while a gate is up. After the sign-in screen the driver may be someone else
  // (or the same one, signed out from the truck screen): the app comes back on the map, not on the screen the
  // previous sign-in ended on (the map then sends a driver who is not onboarded to onboarding, as on first launch).
  const router = useRouter();
  const pathname = usePathname();
  const signedInAnew = useRef(false);
  useEffect(() => {
    if (!ready) return;
    if (screen === "signin") signedInAnew.current = true;
    else if (screen === "app" && signedInAnew.current) {
      signedInAnew.current = false;
      if (pathname !== "/") {
        if (router.canDismiss()) router.dismissAll();
        router.replace("/");
      }
    }
  }, [screen, ready, router, pathname]);
  if (!ready) return null;

  // One app level ErrorBoundary; a render crash shows a reload screen
  // instead of a blank app. GestureHandlerRootView must wrap the whole tree
  // or any gesture/swipe/drawer screen crashes in Expo Go.
  return (
    // The root carries the theme background: with the status bar translucent, what shows behind it is this
    // view, and it was white in dark mode, a bright band above every dark screen (QA 2026-10-31, Н24).
    <GestureHandlerRootView style={[styles.root, { backgroundColor: colors.surface }]}>
      <ErrorBoundary>
        <QueryClientProvider client={queryClient}>
          <KeyboardProvider>
            {/* Reanimated 4 under the system's "remove animations" (Android: TRANSITION_ANIMATION_SCALE 0) leaves a
                view with an `entering` animation invisible on its first mount: the truck form of the onboarding
                came up empty 4 times out of 5 (QA 2026-10-30), the first question before it (#69). Our entering
                animations are 200-320 ms fades, so they always play; this replaces the per-view flag from #69. */}
            <ReducedMotionConfig mode={ReduceMotion.Never} />
            <StatusBar style={scheme === "dark" ? "light" : "dark"} />
            {screen === "language" ? (
              <LanguageGate />
            ) : screen === "intro" ? (
              <IntroGate />
            ) : screen === "signin" ? (
              <SignInGate />
            ) : screen === "consent" ? (
              <ConsentGate />
            ) : screen === "welcome" ? (
              <WelcomeBack />
            ) : (
              <Stack screenOptions={{ headerShown: false }} />
            )}
          </KeyboardProvider>
        </QueryClientProvider>
      </ErrorBoundary>
    </GestureHandlerRootView>
  );
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
  },
});

// Sentry.wrap adds the navigation/touch instrumentation and reports render errors from the tree.
export default Sentry.wrap(RootLayout);
