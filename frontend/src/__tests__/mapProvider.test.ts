import { MAP_PROVIDER, requestedProvider, resolveProvider } from "../map/provider";

// src/map/provider.ts calls loadMapLibre() at import; under jest the native module is absent and
// the loader answers null, so the module-level MAP_PROVIDER is "apple" whatever the env says.
jest.mock("../map/libre", () => ({ loadMapLibre: () => null }));

describe("map provider switch", () => {
  test("apple by default, for an empty or unknown value", () => {
    expect(requestedProvider(undefined)).toBe("apple");
    expect(requestedProvider("")).toBe("apple");
    expect(requestedProvider("google")).toBe("apple");
    expect(resolveProvider(undefined, true)).toBe("apple");
  });

  test("maplibre only when the env says so and the module loaded", () => {
    expect(requestedProvider("maplibre")).toBe("maplibre");
    expect(requestedProvider(" MapLibre ")).toBe("maplibre");
    expect(resolveProvider("maplibre", true)).toBe("maplibre");
    expect(resolveProvider("maplibre", false)).toBe("apple"); // Expo Go: asked for, not available
  });

  test("the module-level value under jest (no native module) is apple", () => {
    expect(MAP_PROVIDER).toBe("apple");
  });
});
