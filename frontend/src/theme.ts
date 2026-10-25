// Design tokens for TruckBuddy. Light theme only (light map, white chips/sheet).
// Keys match the "color" block of /app/design_guidelines.json.
// A plain key is a background; its `on` partner is the text/icon color on top of it.

import { useMemo } from "react";
import { Appearance, StyleSheet, useColorScheme } from "react-native";

export type ColorScheme = "light" | "dark";

const light = {
  // Surfaces
  surface: "#FFFFFF",
  onSurface: "#1C1C1E",
  surfaceSecondary: "#F2F2F7",
  onSurfaceSecondary: "#3A3A3C",
  surfaceTertiary: "#E5E5EA",
  onSurfaceTertiary: "#8E8E93",
  surfaceInverse: "#1C1C1E",
  onSurfaceInverse: "#FFFFFF",
  muted: "#8E8E93",

  // Brand
  brand: "#1C1C1E",
  onBrand: "#FFFFFF",
  brandPrimary: "#1C1C1E",
  onBrandPrimary: "#FFFFFF",
  brandSecondary: "#F2F2F7",
  onBrandSecondary: "#1C1C1E",
  brandTertiary: "#E5E5EA",
  onBrandTertiary: "#1C1C1E",

  // Status
  success: "#34C759",
  onSuccess: "#FFFFFF",
  warning: "#FF9500",
  onWarning: "#FFFFFF",
  error: "#FF3B30",
  onError: "#FFFFFF",
  info: "#007AFF",
  onInfo: "#FFFFFF",

  // Lines
  border: "#E5E5EA",
  borderStrong: "#C7C7CC",
  divider: "#E5E5EA",

  // App specific
  truckStop: "#007AFF",
  hotel: "#AF52DE",
  restArea: "#34C759",
  onCategory: "#FFFFFF",
  routeLine: "#007AFF",
  routeCasing: "#FFFFFF",
  avatarGlow: "#007AFF",
  avatarGlowSoft: "rgba(0,122,255,0.16)",
  avatarBg: "#F7F2EA",
  userHalo: "rgba(0,122,255,0.18)",
  mapPlaceholder: "#EEF1F4",
  shadow: "#000000",
  scrim: "rgba(0,0,0,0.04)",
};

export type ThemeColors = typeof light;

export const defaultScheme = "light" satisfies ColorScheme;

export const themes: { light: ThemeColors; dark?: ThemeColors } = { light };

export function setColorScheme(scheme: ColorScheme | null) {
  Appearance.setColorScheme?.(scheme ?? "unspecified");
}

setColorScheme?.(themes.dark ? null : defaultScheme);

export function useTheme(): { scheme: ColorScheme; colors: ThemeColors } {
  const system = useColorScheme();
  const scheme: ColorScheme = (system === "light" || system === "dark") && themes[system] ? system : defaultScheme;
  return { scheme, colors: themes[scheme] ?? themes.light };
}

export const colors = light;

export const spacing = { xs: 4, sm: 8, md: 12, lg: 16, xl: 24, xxl: 32, xxxl: 48 };
export const radius = { sm: 6, md: 12, lg: 20, pill: 999 };
export const fonts = {
  regular: "PlusJakartaSans-Regular",
  medium: "PlusJakartaSans-Medium",
  semibold: "PlusJakartaSans-SemiBold",
  bold: "PlusJakartaSans-Bold",
  extrabold: "PlusJakartaSans-ExtraBold",
};

export function shadow(c: ThemeColors, level: 1 | 2 = 1) {
  return {
    shadowColor: c.shadow,
    shadowOpacity: level === 1 ? 0.12 : 0.18,
    shadowRadius: level === 1 ? 8 : 16,
    shadowOffset: { width: 0, height: level === 1 ? 2 : 6 },
    elevation: level === 1 ? 4 : 10,
  };
}

export function makeStyles<T extends StyleSheet.NamedStyles<T> | StyleSheet.NamedStyles<any>>(
  factory: (colors: ThemeColors) => T & StyleSheet.NamedStyles<any>,
): () => T {
  return function useStyles(): T {
    const { colors } = useTheme();
    return useMemo(() => StyleSheet.create(factory(colors)), [colors]);
  };
}
