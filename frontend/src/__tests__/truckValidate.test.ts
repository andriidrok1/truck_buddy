import { truckErrors, truckFromInputs } from "../truck/validate";

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
