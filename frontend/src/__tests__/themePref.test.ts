import { resolveScheme, isThemePref } from "../theme";

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
});
