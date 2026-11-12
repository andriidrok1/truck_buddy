import AsyncStorage from "@react-native-async-storage/async-storage";

import { act, renderHook } from "@testing-library/react-native";

import * as theme from "../theme";
import { DEFAULT_THEME_PREF, isThemePref, resolveScheme } from "../theme";

// The phone's system theme, switchable mid-test (stands in for the driver flipping dark mode in Settings).
let mockSystem: "light" | "dark" | null = "light";
const mockSystemListeners = new Set<() => void>();
jest.mock("react-native/Libraries/Utilities/useColorScheme", () => {
  const { useSyncExternalStore } = jest.requireActual("react");
  const useMockColorScheme = () =>
    useSyncExternalStore(
      (l: () => void) => {
        mockSystemListeners.add(l);
        return () => mockSystemListeners.delete(l);
      },
      () => mockSystem,
    );
  return { __esModule: true, default: useMockColorScheme };
});
const setSystem = (s: "light" | "dark" | null) => {
  mockSystem = s;
  mockSystemListeners.forEach((l) => l());
};

describe("theme preference", () => {
  test("Light and Dark win over the system; Auto follows it, light when unknown", () => {
    expect(resolveScheme("light", "dark")).toBe("light");
    expect(resolveScheme("dark", "light")).toBe("dark");
    expect(resolveScheme("system", "dark")).toBe("dark");
    expect(resolveScheme("system", "light")).toBe("light");
    expect(resolveScheme("system", null)).toBe("light");
  });

  test("stored values are validated", () => {
    expect(isThemePref("dark")).toBe(true);
    expect(isThemePref("system")).toBe(true);
    expect(isThemePref("blue")).toBe(false);
    expect(isThemePref(undefined)).toBe(false);
  });

  test("the fresh-install default is Auto (owner's call, 2026-11-12)", () => {
    expect(DEFAULT_THEME_PREF).toBe("system");
  });
});

describe("useTheme", () => {
  // Each test starts as a fresh install: nothing stored, the in-memory preference at its default.
  beforeEach(async () => {
    await AsyncStorage.clear();
    await theme.forgetThemePref();
    setSystem("light");
  });

  test("nothing stored + system dark: dark, and it follows the system live", async () => {
    setSystem("dark");
    expect(await theme.loadThemePref()).toBe("system");
    const { result } = await renderHook(() => theme.useTheme());
    expect(result.current.scheme).toBe("dark");
    expect(result.current.colors).toBe(theme.themes.dark);

    await act(async () => setSystem("light"));
    expect(result.current.scheme).toBe("light");
    await act(async () => setSystem("dark"));
    expect(result.current.scheme).toBe("dark");
  });

  test("an explicit Light in the profile wins over a dark system", async () => {
    theme.setThemePref("light"); // what the profile's Light button does
    setSystem("dark");
    expect(await theme.loadThemePref()).toBe("light");
    const { result } = await renderHook(() => theme.useTheme());
    expect(result.current.scheme).toBe("light");

    await act(async () => theme.setThemePref("dark"));
    expect(result.current.scheme).toBe("dark");
    await act(async () => theme.setThemePref("system"));
    await act(async () => setSystem("light"));
    expect(result.current.scheme).toBe("light");
  });
});
