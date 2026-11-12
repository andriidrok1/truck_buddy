import { heardTruckLine, saidHeight, truckErrors, truckFromInputs } from "../truck/validate";

const ok = { feet: "13", inches: "6", trailer: "53", weight: "80000" };

test("a good truck has no errors and becomes the profile's sizes", () => {
  expect(truckErrors(ok)).toEqual({});
  expect(truckFromInputs(ok)).toEqual({ height_in: 162, trailer_ft: 53, weight_lb: 80000 });
  expect(truckFromInputs({ ...ok, inches: "" })).toEqual({ height_in: 156, trailer_ft: 53, weight_lb: 80000 });
});

test("each field says what is wrong with it and its own limits", () => {
  expect(truckErrors({ ...ok, trailer: "80" })).toEqual({ trailer: "Trailer must be 10 to 65 ft." });
  expect(truckErrors({ ...ok, weight: "800000" })).toEqual({ weight: "Gross weight must be 5,000 to 200,000 lb." });
  expect(truckErrors({ ...ok, feet: "18" })).toEqual({ height: "Height must be 5 ft to 16 ft 11 in." });
  expect(truckErrors({ ...ok, inches: "14" }).height).toBeDefined();
  expect(Object.keys(truckErrors({ feet: "", inches: "", trailer: "", weight: "" })).sort()).toEqual(["height", "trailer", "weight"]);
  expect(truckFromInputs({ ...ok, trailer: "80" })).toBeNull();
});

test("the heard line shows only what the driver said, never a prefilled default", () => {
  expect(heardTruckLine(["height_in", "trailer_ft", "weight_lb", "hazmat"], { ...ok, feet: "14", inches: "0" }, true)).toBe("14 ft 0 in · 53 ft trailer · 80,000 lb · HAZMAT");
  expect(heardTruckLine(["weight_lb"], { ...ok, weight: "72000" }, false)).toBe("72,000 lb");
  expect(heardTruckLine(["height_in", "hazmat"], ok, false)).toBe("13 ft 6 in"); // "no hazmat" adds nothing
  expect(heardTruckLine(["hazmat"], ok, false)).toBe("");
  expect(heardTruckLine([], ok, true)).toBe("");
});

test("saidHeight: only the driver's own words count as a height", () => {
  for (const yes of ["13 foot 6", "13'6\"", "It's 13 6, 53 foot trailer, 80,000 pounds", "thirteen six", "Fourteen feet", "13.6", "тринадцать шесть", "десять футов", "высота 13 и 6", "about 12 feet tall"]) {
    expect([yes, saidHeight(yes)]).toEqual([yes, true]);
  }
  for (const no of ["80,000 pounds, no hazmat", "53 foot trailer", "no hazmat", "14,000 pounds", "восемьдесят тысяч фунтов", "trailer is 48, weight 72000", ""]) {
    expect([no, saidHeight(no)]).toEqual([no, false]);
  }
});
