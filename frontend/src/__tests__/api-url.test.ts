import { absoluteUrl, apiUrl } from "../url";

// jest.setup.ts pins EXPO_PUBLIC_BACKEND_URL to https://ci.invalid before any module loads.
const ORIGIN = "https://ci.invalid";

describe("api url helpers", () => {
  test("absoluteUrl prefixes the backend origin", () => {
    expect(absoluteUrl("/audio/x.mp3")).toBe(`${ORIGIN}/audio/x.mp3`);
  });

  test("apiUrl prefixes the backend origin plus /api", () => {
    expect(apiUrl("/agent")).toBe(`${ORIGIN}/api/agent`);
    expect(apiUrl("/places?category=hotel")).toBe(`${ORIGIN}/api/places?category=hotel`);
  });

  test("helpers do not normalise slashes (callers pass a leading slash)", () => {
    expect(absoluteUrl("audio.mp3")).toBe(`${ORIGIN}audio.mp3`);
  });

  test("module refuses to load without a backend URL", () => {
    jest.isolateModules(() => {
      const saved = process.env.EXPO_PUBLIC_BACKEND_URL;
      delete process.env.EXPO_PUBLIC_BACKEND_URL;
      try {
        expect(() => jest.requireActual("../url")).toThrow(/EXPO_PUBLIC_BACKEND_URL is not set/);
      } finally {
        process.env.EXPO_PUBLIC_BACKEND_URL = saved;
      }
    });
  });
});
