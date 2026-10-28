import { storage } from "../utils/storage";

import { ELD_HEADER, eldHeader, getEldToken, loadEldToken, setEldToken } from "../eld";

// Storage is mocked so the key never reaches a real Keychain; the header logic is what matters.
// (jest.mock is hoisted above the imports; the `mock` prefix lets the factory see the map.)
const mockStore = new Map<string, string>();
jest.mock("../utils/storage", () => ({
  storage: {
    secureGet: jest.fn(async (key: string, fallback: unknown) => (mockStore.has(key) ? JSON.parse(mockStore.get(key)!) : fallback)),
    secureSet: jest.fn(async (key: string, value: unknown) => {
      mockStore.set(key, JSON.stringify(value));
      return true;
    }),
    secureRemove: jest.fn(async (key: string) => mockStore.delete(key)),
  },
}));

describe("eld", () => {
  beforeEach(() => mockStore.clear());

  test("no key stored: header absent", async () => {
    await loadEldToken();
    expect(eldHeader()).toEqual({});
    expect(getEldToken()).toBeNull();
  });

  test("saved key: header present, trimmed, under eld_token_v1", async () => {
    await setEldToken("  abc123  ");
    expect(eldHeader()).toEqual({ [ELD_HEADER]: "abc123" });
    expect(storage.secureSet).toHaveBeenCalledWith("eld_token_v1", "abc123");
    expect(mockStore.has("eld_token_v1")).toBe(true);
  });

  test("removed key: header absent again and the secure item is gone", async () => {
    await setEldToken("abc123");
    await setEldToken(null);
    expect(eldHeader()).toEqual({});
    expect(storage.secureRemove).toHaveBeenCalledWith("eld_token_v1");
    expect(mockStore.has("eld_token_v1")).toBe(false);
  });

  test("blank input counts as removal", async () => {
    await setEldToken("abc123");
    await setEldToken("   ");
    expect(eldHeader()).toEqual({});
  });

  test("header is a fresh object each call (safe to spread)", async () => {
    await setEldToken("k");
    const a = eldHeader();
    a["X-Other"] = "x";
    expect(eldHeader()).toEqual({ [ELD_HEADER]: "k" });
  });
});
