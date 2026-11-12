// Russian QA 2026-11-11 (vault: 40-Events/VibeCon-SF-2026/TruckBuddy — QA русской версии, второй проход): what the
// driver hears. Every Russian line is asserted whole; English must come out exactly as before.
import { announcement, maneuverWords, type StageKey } from "../geo/guidance";
import { t } from "../i18n";

// What the speech queue does with a line before it is spoken (src/hooks/useSpeechQueue.ts).
const spoken = (line: string) => t(line, undefined, "ru");
const HAS_LATIN_WORD = /[A-Za-z]{2,}/;

describe("arrival in Russian (the server's instruction is already Russian)", () => {
  const here = { action: "arrive", instruction: "Ты на месте" };
  const named = { action: "arrive", instruction: "Прибытие: Pilot Travel Center, справа" };

  test("a mile and half a mile out it is one sentence, not a lead-in glued to the arrival line", () => {
    expect(spoken(announcement(here, "far", 1609, "ru"))).toBe("Через милю будешь на месте."); // was "Через милю ты на месте."
    expect(spoken(announcement(here, "near", 800, "ru"))).toBe("Через полмили будешь на месте.");
    expect(spoken(announcement(named, "near", 800, "ru"))).toBe("Через полмили будешь на месте."); // was "Через полмили прибытие: Pilot…"
    expect(spoken(announcement(named, "far", 1500, "ru"))).toBe("Через 0,9 мили будешь на месте.");
    expect(spoken(announcement(named, "near", 183, "ru"))).toBe("Через 600 футов будешь на месте.");
  });

  test("at the end the server's own line is said", () => {
    expect(spoken(announcement(here, "now", 40, "ru"))).toBe("Ты на месте.");
    expect(spoken(announcement(named, "now", 40, "ru"))).toBe("Прибытие: Pilot Travel Center, справа.");
  });

  test("an arrival the server left in English is still Russian at every stage", () => {
    const en = { action: "arrive", instruction: "Arrive at your destination on the left" };
    expect(spoken(announcement(en, "far", 1609, "ru"))).toBe("Через милю будешь на месте.");
    expect(spoken(announcement(en, "near", 800, "ru"))).toBe("Через полмили будешь на месте.");
    expect(spoken(announcement(en, "now", 40, "ru"))).toBe("Ты на месте.");
  });
});

describe("a maneuver the server left in English, in Russian mode", () => {
  const turn = { action: "turn", direction: "left", instruction: "Turn left onto Main St" };

  test("said by its action and direction: no English words inside a Russian sentence", () => {
    expect(spoken(announcement(turn, "near", 152, "ru"))).toBe("Через 500 футов поверни налево."); // was "Через 500 футов turn left onto Main St."
    expect(spoken(announcement(turn, "far", 1609, "ru"))).toBe("Через милю поверни налево.");
    expect(spoken(announcement(turn, "now", 40, "ru"))).toBe("Поверни налево."); // was the English line, read by the Russian voice
    const uturn = { action: "uTurn", direction: "left", instruction: "Make a U-Turn onto West Capitol Ave" };
    expect(spoken(announcement(uturn, "near", 800, "ru"))).toBe("Через полмили развернись.");
    expect(spoken(announcement(uturn, "now", 40, "ru"))).toBe("Развернись.");
  });

  test("every action and direction the router sends gives a whole Russian sentence at every stage", () => {
    const actions = ["turn", "keep", "continueHighway", "exit", "ramp", "enterHighway", "uTurn", "roundaboutEnter", "roundaboutPass", "roundaboutExit", "ferry", "continue", "depart", "somethingNew"];
    const stages: [StageKey, number][] = [["far", 1500], ["near", 300], ["now", 40]];
    for (const action of actions) {
      for (const direction of ["left", "right", "middle", null, undefined]) {
        const words = maneuverWords(action, direction);
        expect({ words, ru: HAS_LATIN_WORD.test(t(words, undefined, "ru")) }).toEqual({ words, ru: false });
        for (const [stage, m] of stages) {
          const line = spoken(announcement({ action, direction, instruction: "Take the thing onto Some Rd" }, stage, m, "ru"));
          expect({ action, direction, stage, latin: HAS_LATIN_WORD.test(line) }).toEqual({ action, direction, stage, latin: false });
          expect(line).toMatch(/^[А-ЯЁ].*\.$/);
        }
      }
    }
  });

  test("a Russian instruction takes the usual path, with its road name", () => {
    const ru = { action: "turn", direction: "left", instruction: "Поверни налево на Main St" };
    expect(spoken(announcement(ru, "near", 152, "ru"))).toBe("Через 500 футов поверни налево на Main St.");
    expect(spoken(announcement(ru, "now", 40, "ru"))).toBe("Поверни налево на Main St.");
    expect(spoken(announcement({ action: "exit", direction: "right", instruction: "Съезд 522 в сторону Auburn" }, "far", 1609, "ru"))).toBe("Через милю съезд 522 в сторону Auburn.");
  });
});

describe("English and Ukrainian are untouched", () => {
  const turn = { action: "turn", direction: "left", instruction: "Turn left onto Main St" };
  const arrive = { action: "arrive", instruction: "Arrive at Pilot Travel Center on the right" };
  test.each(["en", "uk"] as const)("%s: the same sentences as before", (lang) => {
    expect(announcement(turn, "near", 152, lang)).toBe("In 500 feet, turn left onto Main St.");
    expect(announcement(turn, "now", 40, lang)).toBe("Turn left onto Main St.");
    expect(announcement(arrive, "far", 1500, lang)).toBe("In 0.9 miles, you'll arrive at Pilot Travel Center on the right.");
    expect(announcement(arrive, "now", 40, lang)).toBe("Arrive at Pilot Travel Center on the right.");
    // and t() leaves them alone, so a Ukrainian phone never hears Russian
    expect(t(announcement(arrive, "far", 1500, lang), undefined, lang)).toBe("In 0.9 miles, you'll arrive at Pilot Travel Center on the right.");
  });
});
