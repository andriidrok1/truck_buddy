import { buildSttHint, STT_HINT_MAX_CHARS, streetFromInstruction } from "../voice/sttHint";

describe("buildSttHint", () => {
  test("joins names with commas, keeping the order given", () => {
    expect(buildSttHint(["Pilot Fernley", "I-80 E", "Reno"])).toBe("Pilot Fernley, I-80 E, Reno");
  });

  test("dedupes case-insensitively and drops empties", () => {
    expect(buildSttHint(["Pilot Fernley", "pilot fernley", "", "  ", null, undefined, "PILOT FERNLEY", "Reno"])).toBe("Pilot Fernley, Reno");
  });

  test("strips commas inside names (the comma is the separator)", () => {
    expect(buildSttHint(["Reno, NV", "Love's Travel Stop, Sparks"])).toBe("Reno NV, Love's Travel Stop Sparks");
  });

  test("caps the joined string, skipping names that would not fit", () => {
    const long = "x".repeat(STT_HINT_MAX_CHARS - 10);
    const out = buildSttHint([long, "y".repeat(20), "short"]);
    expect(out.length).toBeLessThanOrEqual(STT_HINT_MAX_CHARS);
    expect(out).toBe(`${long}, short`);
    expect(buildSttHint(["abc", "defg", "hi"], 9)).toBe("abc, defg"); // 9 chars exactly
  });

  test("empty input is an empty string", () => {
    expect(buildSttHint([])).toBe("");
  });
});

describe("streetFromInstruction", () => {
  test("takes the road after onto / toward", () => {
    expect(streetFromInstruction("Turn right onto I-80 E.")).toBe("I-80 E");
    expect(streetFromInstruction("Keep left toward Reno")).toBe("Reno");
  });

  test("null without a road", () => {
    expect(streetFromInstruction("Continue straight.")).toBeNull();
    expect(streetFromInstruction(undefined)).toBeNull();
  });
});
