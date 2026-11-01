import { QueryClientProvider } from "@tanstack/react-query";
import { useFonts } from "expo-font";
import { Stack } from "expo-router";
import { StatusBar } from "expo-status-bar";
import * as SystemUI from "expo-system-ui";
import { useEffect, useState } from "react";
import { LogBox, StyleSheet } from "react-native";
import { GestureHandlerRootView } from "react-native-gesture-handler";
import { ReducedMotionConfig, ReduceMotion } from "react-native-reanimated";
import { KeyboardProvider } from "@/src/keyboard";

import { useAuthState } from "@/src/auth";
import { loadThemePref, useTheme } from "@/src/theme";
import { ConsentGate } from "@/src/components/ConsentGate";
import { ensureTruckProfile } from "@/src/hooks/useTruckProfile";
import { DeviceGate } from "@/src/components/DeviceGate";
import { ErrorBoundary } from "@/src/components/error-boundary";
import { useConsent } from "@/src/consent";
import { loadEldToken } from "@/src/eld";
import { queryClient } from "@/src/query-client";

// Disable logbox errors etc so that users can see the app
// and agent works as expected.
LogBox.ignoreAllLogs(true);

export default function RootLayout() {
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
  const auth = useAuthState();
  const consent = useConsent();
  // The Motive key is read alongside the auth token so the first copilot request already carries it.
  useEffect(() => {
    loadEldToken();
  }, []);
  // The truck profile too: every request awaits it, but reading it here means it is in before the map
  // screen even mounts, so nothing is ever planned for the 13'6" default on a taller rig.
  useEffect(() => {
    ensureTruckProfile();
  }, []);
  // Pre-load the icon font (prevents blank icons in Expo Go) and app fonts.
  const [loaded, error] = useFonts({
    MaterialDesignIcons: require("@react-native-vector-icons/material-design-icons/fonts/MaterialDesignIcons.ttf"),
    "PlusJakartaSans-Regular": require("../assets/fonts/PlusJakartaSans-Regular.ttf"),
    "PlusJakartaSans-Medium": require("../assets/fonts/PlusJakartaSans-Medium.ttf"),
    "PlusJakartaSans-SemiBold": require("../assets/fonts/PlusJakartaSans-SemiBold.ttf"),
    "PlusJakartaSans-Bold": require("../assets/fonts/PlusJakartaSans-Bold.ttf"),
    "PlusJakartaSans-ExtraBold": require("../assets/fonts/PlusJakartaSans-ExtraBold.ttf"),
  });

  if ((!loaded && !error) || auth === "loading" || consent === "loading" || !themeReady) return null;

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
            {consent !== "yes" ? <ConsentGate /> : auth !== "in" ? <DeviceGate /> : <Stack screenOptions={{ headerShown: false }} />}
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
