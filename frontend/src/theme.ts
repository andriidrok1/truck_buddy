// Design tokens for TruckBuddy, light and dark. The scheme follows the system (a driver's phone goes
// dark at night; until 2026-10-29 the app stayed white, UI review B-UI-3).
// Keys match the "color" block of /app/design_guidelines.json.
// A plain key is a background; its `on` partner is the text/icon color on top of it.

import { useMemo, useSyncExternalStore } from "react";
import { Appearance, StyleSheet, useColorScheme, type ImageStyle, type TextStyle, type ViewStyle } from "react-native";

import { storage } from "./utils/storage";

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
  walmart: "#0071CE",
  catScale: "#FF9500",
  truckWash: "#32ADE6",
  weighStation: "#636366",
  onCategory: "#FFFFFF",
  routeLine: "#007AFF",
  routeCasing: "#FFFFFF",
  trafficSlow: "#FFC400",
  trafficJam: "#E53935",
  avatarGlow: "#007AFF",
  avatarGlowSoft: "rgba(0,122,255,0.16)",
  avatarBg: "#F7F2EA",
  userHalo: "rgba(0,122,255,0.18)",
  mapPlaceholder: "#EEF1F4",
  shadow: "#000000",
  scrim: "rgba(0,0,0,0.04)",
};

export type ThemeColors = typeof light;

// Dark: iOS system greys for the surfaces, the brighter dark-mode variants of the status colours (they
// read on #1C1C1E), the "black" buttons become light, the route keeps its blue with a dark casing.
const dark: ThemeColors = {
  surface: "#1C1C1E",
  onSurface: "#F2F2F7",
  surfaceSecondary: "#2C2C2E",
  onSurfaceSecondary: "#D1D1D6",
  surfaceTertiary: "#3A3A3C",
  onSurfaceTertiary: "#98989D",
  surfaceInverse: "#F2F2F7",
  onSurfaceInverse: "#1C1C1E",
  muted: "#98989D",

  brand: "#F2F2F7",
  onBrand: "#1C1C1E",
  brandPrimary: "#F2F2F7",
  onBrandPrimary: "#1C1C1E",
  brandSecondary: "#2C2C2E",
  onBrandSecondary: "#F2F2F7",
  brandTertiary: "#3A3A3C",
  onBrandTertiary: "#F2F2F7",

  success: "#30D158",
  onSuccess: "#0B1F10",
  warning: "#FF9F0A",
  onWarning: "#1C1C1E",
  error: "#FF453A",
  onError: "#FFFFFF",
  info: "#0A84FF",
  onInfo: "#FFFFFF",

  border: "#3A3A3C",
  borderStrong: "#48484A",
  divider: "#3A3A3C",

  truckStop: "#0A84FF",
  hotel: "#BF5AF2",
  restArea: "#30D158",
  walmart: "#3B8CE6",
  catScale: "#FF9F0A",
  truckWash: "#64D2FF",
  weighStation: "#98989D",
  onCategory: "#FFFFFF",
  routeLine: "#0A84FF",
  routeCasing: "#0E0E10",
  trafficSlow: "#FFD60A",
  trafficJam: "#FF453A",
  avatarGlow: "#0A84FF",
  avatarGlowSoft: "rgba(10,132,255,0.22)",
  avatarBg: "#3A332B",
  userHalo: "rgba(10,132,255,0.25)",
  mapPlaceholder: "#121214",
  shadow: "#000000",
  scrim: "rgba(0,0,0,0.4)",
};

export const defaultScheme = "light" satisfies ColorScheme;

export const themes: { light: ThemeColors; dark: ThemeColors } = { light, dark };

/** Force a scheme at the platform level; null = back to the system. Kept for tooling, the app uses the preference below. */
export function setColorScheme(scheme: ColorScheme | null) {
  Appearance.setColorScheme?.(scheme ?? "unspecified");
}

// The driver's choice, from the profile screen: Light (the default, the owner's call), Dark, or Auto
// (follow the system). Stored on the phone; loaded once at startup before the first screen renders.
export type ThemePref = ColorScheme | "system";
const PREF_KEY = "theme_pref_v1";
let pref: ThemePref = "light";
let prefLoaded = false;
const listeners = new Set<() => void>();
const subscribe = (l: () => void) => {
  listeners.add(l);
  return () => listeners.delete(l);
};
const emit = () => listeners.forEach((l) => l());

export const isThemePref = (v: unknown): v is ThemePref => v === "light" || v === "dark" || v === "system";

export async function loadThemePref(): Promise<ThemePref> {
  if (!prefLoaded) {
    try {
      const v = await storage.getItem(PREF_KEY, "light" as string);
      pref = isThemePref(v) ? v : "light";
    } catch {
      pref = "light";
    }
    prefLoaded = true;
    emit();
  }
  return pref;
}

export function setThemePref(p: ThemePref) {
  pref = p;
  emit();
  storage.setItem(PREF_KEY, p).catch(() => {});
}

export function useThemePref(): ThemePref {
  return useSyncExternalStore(subscribe, () => pref, () => pref);
}

/** The scheme in force for a preference and the system's scheme. */
export function resolveScheme(p: ThemePref, system: string | null | undefined): ColorScheme {
  if (p === "system") return system === "dark" ? "dark" : "light";
  return p;
}

export function useTheme(): { scheme: ColorScheme; colors: ThemeColors } {
  const p = useThemePref();
  const system = useColorScheme();
  const scheme = resolveScheme(p, system);
  return { scheme, colors: themes[scheme] };
}

/** Light tokens for code that cannot use the hook (tests, constants); components use useTheme(). */
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

type NamedStyles<T> = { [P in keyof T]: ViewStyle | TextStyle | ImageStyle };

export function makeStyles<T extends NamedStyles<T>>(factory: (colors: ThemeColors) => T): () => T {
  return function useStyles(): T {
    const { colors } = useTheme();
    return useMemo(() => StyleSheet.create(factory(colors)), [colors]);
  };
}
