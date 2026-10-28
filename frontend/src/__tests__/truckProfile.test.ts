import { DEFAULT_TRUCK, parseStoredProfile, type TruckProfile } from "../hooks/useTruckProfile";

const profile: TruckProfile = { ...DEFAULT_TRUCK, height_in: 158, driver_name: "Ray", favorite_chains: ["Pilot"] };

describe("parseStoredProfile", () => {
  test("plain object (current builds save the profile itself)", () => {
    expect(parseStoredProfile(profile)).toEqual(profile);
  });

  test("single-encoded JSON string", () => {
    expect(parseStoredProfile(JSON.stringify(profile))).toEqual(profile);
  });

  test("double-encoded JSON string (older builds stringified before the wrapper did)", () => {
    expect(parseStoredProfile(JSON.stringify(JSON.stringify(profile)))).toEqual(profile);
  });

  test("missing key", () => {
    expect(parseStoredProfile(null)).toBeNull();
    expect(parseStoredProfile(undefined)).toBeNull();
  });

  test("corrupt or wrong-shaped values fall back to null", () => {
    expect(parseStoredProfile("{not json")).toBeNull();
    expect(parseStoredProfile('"\\"{still not json"')).toBeNull();
    expect(parseStoredProfile("42")).toBeNull();
    expect(parseStoredProfile(JSON.stringify([1, 2]))).toBeNull();
    expect(parseStoredProfile(7)).toBeNull();
  });
});
