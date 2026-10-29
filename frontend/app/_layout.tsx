import { QueryClientProvider } from "@tanstack/react-query";
import { useFonts } from "expo-font";
import { Stack } from "expo-router";
import { StatusBar } from "expo-status-bar";
import { useEffect } from "react";
import { LogBox, StyleSheet } from "react-native";
import { GestureHandlerRootView } from "react-native-gesture-handler";
import { KeyboardProvider } from "@/src/keyboard";

import { useAuthState } from "@/src/auth";
import { useTheme } from "@/src/theme";
import { ConsentGate } from "@/src/components/ConsentGate";
import { DeviceGate } from "@/src/components/DeviceGate";
import { ErrorBoundary } from "@/src/components/error-boundary";
import { useConsent } from "@/src/consent";
import { loadEldToken } from "@/src/eld";
import { queryClient } from "@/src/query-client";

// Disable logbox errors etc so that users can see the app
// and agent works as expected.
LogBox.ignoreAllLogs(true);

export default function RootLayout() {
  const { scheme } = useTheme();
  const auth = useAuthState();
  const consent = useConsent();
  // The Motive key is read alongside the auth token so the first copilot request already carries it.
  useEffect(() => {
    loadEldToken();
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

  if ((!loaded && !error) || auth === "loading" || consent === "loading") return null;

  // One app level ErrorBoundary; a render crash shows a reload screen
  // instead of a blank app. GestureHandlerRootView must wrap the whole tree
  // or any gesture/swipe/drawer screen crashes in Expo Go.
  return (
    <GestureHandlerRootView style={styles.root}>
      <ErrorBoundary>
        <QueryClientProvider client={queryClient}>
          <KeyboardProvider>
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
