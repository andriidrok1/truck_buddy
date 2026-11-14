// The turn banner's short form of a long instruction (QA 2026-11-09 and 2026-11-11: the tail with the exit's
// direction was cut off).
import { bannerInstruction, LONG_INSTRUCTION } from "../geo/bannerInstruction";

describe("bannerInstruction", () => {
  test.each([
    "Turn left onto J St",
    "Поверни налево на J St",
    "Take exit 7 for Stockton Blvd",
    "Держись правее в сторону I-5 N",
    "x".repeat(LONG_INSTRUCTION),
  ])("a short instruction is left as it is: %s", (s) => {
    expect(bannerInstruction(s)).toBe(s);
  });

  test.each([
    ["Take exit 52B toward Sacramento International Airport/Woodland onto I-5 N", "Exit 52B toward Sacramento International Airport"],
    ["Take exit 52B toward Sacramento International Airport / Woodland", "Exit 52B toward Sacramento International Airport"],
    ["Take exit 518 toward US-50 W/San Francisco/I-80 onto I-80-BR W (Capital City Fwy).", "Exit 518 toward US-50 W"],
    ["Съезд 518 в сторону US-50 W/San Francisco/I-80 на I-80-BR W (Capital City Fwy)", "Съезд 518 в сторону US-50 W"],
    ["Съезд 522 в сторону San Francisco на I-80 W / US-50 W", "Съезд 522 в сторону San Francisco"],
    ["Съезд 52B в сторону Sacramento International Airport / Woodland", "Съезд 52B в сторону Sacramento International Airport"],
  ])("an exit keeps its number and first direction: %s", (long, short) => {
    expect(bannerInstruction(long)).toBe(short);
  });

  test("no exit number: the road stays, each sign list keeps its first name", () => {
    expect(bannerInstruction("Keep right onto I-80 E/US-50 E toward Reno/South Lake Tahoe")).toBe("Keep right onto I-80 E toward Reno");
    expect(bannerInstruction("Держись правее на I-80 E/US-50 E в сторону Reno/South Lake Tahoe")).toBe("Держись правее на I-80 E в сторону Reno");
  });

  test("'налево' is not read as 'на'", () => {
    expect(bannerInstruction("Поверни налево на West Capitol Avenue (Business Loop 80)")).toBe("Поверни налево на West Capitol Avenue");
  });
});
