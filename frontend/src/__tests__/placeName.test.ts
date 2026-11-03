import { withoutCountry } from "../utils/placeName";

describe("withoutCountry", () => {
  test("drops the country every place shares", () => {
    expect(withoutCountry("Reno, NV, United States")).toBe("Reno, NV");
    expect(withoutCountry("Reno, NV, USA")).toBe("Reno, NV");
    expect(withoutCountry("Pilot Travel Center, Sacramento")).toBe("Pilot Travel Center, Sacramento");
    expect(withoutCountry("United States Cold Storage")).toBe("United States Cold Storage");
  });
});
