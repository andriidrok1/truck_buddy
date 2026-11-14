// Russian QA 2026-11-11 (vault: 40-Events/VibeCon-SF-2026/TruckBuddy — QA русской версии, второй проход): what the
// driver hears. Every Russian line is asserted whole; English must come out exactly as before.
import { advisoryText, type Advisory } from "../geo/advisories";
import { announcement, maneuverFacts, maneuverWords, type StageKey } from "../geo/guidance";
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
  const say = (action: string, direction: string | null, instruction: string, stage: StageKey = "now", m = 40) =>
    spoken(announcement({ action, direction, instruction }, stage, m, "ru"));

  test("said in Russian by its action and direction, with the road it names", () => {
    expect(spoken(announcement(turn, "near", 152, "ru"))).toBe("Через 500 футов поверни налево на Main St."); // was "Через 500 футов turn left onto Main St."
    expect(spoken(announcement(turn, "far", 1609, "ru"))).toBe("Через милю поверни налево на Main St.");
    expect(spoken(announcement(turn, "now", 40, "ru"))).toBe("Поверни налево на Main St."); // was the English line, read by the Russian voice
    expect(say("uTurn", "left", "Make a U-Turn onto West Capitol Ave", "near", 800)).toBe("Через полмили развернись на West Capitol Ave.");
    expect(say("turn", "right", "Turn right onto N Virginia St (US-395-BR) toward S Virginia St/Downtown.")).toBe(
      "Поверни направо на N Virginia St (US-395-BR) в сторону S Virginia St/Downtown.");
  });

  test("exits keep their number, road and direction: two exits in a row do not sound the same", () => {
    expect(say("exit", "right", "Take exit 522 toward San Francisco onto I-80 W", "far", 1609)).toBe("Через милю съезд 522 справа, на I-80 W, в сторону San Francisco.");
    expect(say("exit", "right", "Take exit 12B toward Reno/Sacramento onto I-80 W")).toBe("Съезд 12B справа, на I-80 W, в сторону Reno/Sacramento.");
    expect(say("exit", "right", "Take exit 14 toward Virginia St/Downtown.", "near", 800)).toBe("Через полмили съезд 14 справа, в сторону Virginia St/Downtown.");
    expect(say("exit", null, "Take exit 7 for Stockton Blvd")).toBe("Съезд 7, в сторону Stockton Blvd.");
    expect(say("exit", "left", "Take the left exit onto I-80-BR E (Capital City Fwy).")).toBe("Сворачивай на съезд слева, на I-80-BR E (Capital City Fwy).");
    expect(say("exit", "right", "Take the exit toward I-80-TOLL/San Francisco onto I-80 W")).toBe("Сворачивай на съезд справа, на I-80 W, в сторону I-80-TOLL/San Francisco.");
  });

  test("keep, ramp, continue and roundabout", () => {
    expect(say("keep", "right", "Keep right onto I-80 E toward Reno")).toBe("Держись правее, на I-80 E, в сторону Reno.");
    expect(say("keep", "right", "Keep right toward Emerald Bay Rd/US-50", "near", 152)).toBe("Через 500 футов держись правее, в сторону Emerald Bay Rd/US-50.");
    expect(say("keep", "right", "Keep right.")).toBe("Держись правее.");
    expect(say("ramp", null, "Take ramp onto I-5 N toward Redding")).toBe("Заезжай на съезд, на I-5 N, в сторону Redding.");
    expect(say("ramp", "left", "Take left ramp onto I-80-BR W toward US-50 W")).toBe("Заезжай на съезд слева, на I-80-BR W, в сторону US-50 W.");
    expect(say("continue", null, "Continue on CA-89 N toward Truckee")).toBe("Продолжай по CA-89 N, в сторону Truckee.");
    expect(say("roundaboutExit", "right", "Take the 2nd exit from roundabout onto Luther Pass Rd (CA-89 S) toward Markleeville.", "near", 152)).toBe(
      "Через 500 футов на круге 2-й съезд, на Luther Pass Rd (CA-89 S), в сторону Markleeville.");
    expect(say("roundaboutPass", null, "Pass 2 roundabouts and continue on State Route 28")).toBe("Проезжай круг, на State Route 28.");
  });

  test("the facts are read from the words only: nothing found, nothing added", () => {
    expect(maneuverFacts("Take exit 522 toward San Francisco onto I-80 W")).toEqual({ exit: "522", nth: null, road: "I-80 W", toward: "San Francisco" });
    expect(maneuverFacts("Keep right onto I-80 E toward Reno.")).toEqual({ exit: null, nth: null, road: "I-80 E", toward: "Reno" });
    expect(maneuverFacts("Take the 1st exit from roundabout")).toEqual({ exit: null, nth: "1", road: null, toward: null });
    expect(maneuverFacts("Keep right")).toEqual({ exit: null, nth: null, road: null, toward: null });
    expect(maneuverFacts("Your destination is on the left")).toEqual({ exit: null, nth: null, road: null, toward: null });
    expect(say("turn", "left", "Turn left", "near", 152)).toBe("Через 500 футов поверни налево.");
    expect(say("ferry", null, "Take the ferry")).toBe("Заезжай на паром.");
    // An action the table does not know is not dressed up with a road.
    expect(say("somethingNew", null, "Do a thing onto Main St", "near", 152)).toBe("Через 500 футов следуй по маршруту.");
  });

  test("every action and direction the router sends gives a whole Russian sentence at every stage", () => {
    const actions = ["turn", "keep", "continueHighway", "exit", "ramp", "enterHighway", "uTurn", "roundaboutEnter", "roundaboutPass", "roundaboutExit", "ferry", "continue", "depart", "somethingNew"];
    const stages: [StageKey, number][] = [["far", 1500], ["near", 300], ["now", 40]];
    for (const action of actions) {
      for (const direction of ["left", "right", "middle", null, undefined]) {
        const words = maneuverWords(action, direction);
        expect({ words, ru: HAS_LATIN_WORD.test(t(words, undefined, "ru")) }).toEqual({ words, ru: false });
        for (const [stage, m] of stages) {
          // No road or direction in the words: the only Latin left would be an English word that slipped through.
          const line = spoken(announcement({ action, direction, instruction: "Do the thing" }, stage, m, "ru"));
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

describe("what lies ahead, spoken (advisoryText)", () => {
  const adv = (over: Partial<Advisory>): Advisory => ({ id: "x", kind: "incident", text: "", severity: 2, aheadM: 6400, index: 5, ...over });

  test("an English description is not read by the Russian voice: the kind and the distance only", () => {
    // The server sends the kind in Russian with a capital letter; inside the sentence it is lower case.
    expect(advisoryText(adv({ text: "Авария: Multi-vehicle accident on I-80" }), "ru")).toBe("Внимание: авария примерно через 4 мили.");
    expect(advisoryText(adv({ text: "Ремонт дороги: Road construction. Roadway reduced to two lanes", aheadM: 800 }), "ru")).toBe("Внимание: ремонт дороги через полмили.");
    expect(advisoryText(adv({ text: "Происшествие: Major event", aheadM: 800 }), "ru")).toBe("Внимание: происшествие через полмили.");
    // The kind left in English too: the app's own word for it, or a general one.
    expect(advisoryText(adv({ text: "Accident: crash on I-80" }), "ru")).toBe("Внимание: авария примерно через 4 мили.");
    expect(advisoryText(adv({ text: "Other: Major event" }), "ru")).toBe("Внимание: происшествие примерно через 4 мили.");
    expect(advisoryText(adv({ text: "Road works: Major event" }), "ru")).toBe("Внимание: происшествие примерно через 4 мили.");
    expect(advisoryText(adv({ severity: 3, text: "Road closed: US-50 closed at Echo Summit" }), "ru")).toBe("Внимание: дорога закрыта примерно через 4 мили.");
  });

  test("a description the server translated is said whole", () => {
    expect(advisoryText(adv({ text: "Авария: столкновение на I-80" }), "ru")).toBe("Внимание: авария примерно через 4 мили: столкновение на I-80.");
    expect(advisoryText(adv({ severity: 3, text: "Road closed: US-50 закрыта у Echo Summit", aheadM: 800 }), "ru")).toBe("Внимание: дорога закрыта через полмили. US-50 закрыта у Echo Summit.");
  });

  test("weather: a translated event opens the sentence, an untranslated one becomes a general warning", () => {
    expect(advisoryText(adv({ kind: "weather", text: "предупреждение о зимнем шторме" }), "ru")).toBe("Предупреждение о зимнем шторме примерно через 4 мили.");
    // was "Winter Weather Advisory через полмили."
    expect(advisoryText(adv({ kind: "weather", text: "Winter Weather Advisory", aheadM: 800 }), "ru")).toBe("Внимание: погодное предупреждение через полмили.");
  });

  test("English and Ukrainian: the same lines as before", () => {
    for (const lang of ["en", "uk"] as const) {
      expect(advisoryText(adv({ text: "Accident: Multi-vehicle accident on I-80" }), lang)).toBe("Heads up: Accident in about 4 miles: Multi-vehicle accident on I-80.");
      expect(advisoryText(adv({ text: "Other: Major event", aheadM: 800 }), lang)).toBe("Heads up: Major event in half a mile.");
      expect(advisoryText(adv({ severity: 3, text: "Road closed: US-50 closed at Echo Summit" }), lang)).toBe("Heads up: road closed in about 4 miles. US-50 closed at Echo Summit.");
      expect(advisoryText(adv({ kind: "weather", text: "Winter Weather Advisory", aheadM: 800 }), lang)).toBe("Winter Weather Advisory in half a mile.");
    }
  });
});

describe("wording (src/i18n/ru.ts)", () => {
  const ru = (en: string) => t(en, undefined, "ru");

  test("miles per gallon is not called fuel consumption", () => {
    expect(ru("Fuel economy")).toBe("Пробег на галлон");
    expect(ru("Fuel economy must be 3 to 15 mpg.")).toBe("Пробег должен быть от 3 до 15 миль на галлон.");
    expect(ru("For the route cost estimate. Leave empty for 6.5.")).toBe("Нужен, чтобы посчитать стоимость маршрута. Если оставить пустым, считаю по 6,5 мили на галлон.");
  });

  test("no English words left inside Russian sentences of the truck profile", () => {
    expect(ru("Owner-operator. Off: you drive for a company")).toBe("Свой трак. Выключи, если работаешь на компанию");
    expect(ru("Height, trailer, weight, hazmat and your name go back to the defaults. Nothing is saved until you tap Save truck.")).toBe(
      "Высота, трейлер, вес, опасный груз и имя вернутся к значениям по умолчанию. Ничего не сохранится, пока не нажмёшь «Сохранить трак».");
  });

  test("zoom buttons, the notification without a place name, the CAT Scale tag", () => {
    expect(ru("Zoom in")).toBe("Приблизить");
    expect(ru("Zoom out")).toBe("Отдалить");
    expect(t("Navigating to {name}", { name: ru("your stop") }, "ru")).toBe("Едем в пункт назначения"); // was "Едем в your stop"
    expect(t("Navigating to {name}", { name: t("your stop", undefined, "en") }, "en")).toBe("Navigating to your stop");
    expect(ru("CAT Scale")).toBe("Кэт-скейл");
    expect(ru("CAT Scale · Truck diesel · Showers")).toBe("Кэт-скейл · Дизель для траков · Душ");
  });

  test("nothing says the driver is a man", () => {
    expect(ru("You said: “где дизель”")).toBe("Твои слова: «где дизель»");
    // "Agree and enter an access code" is gone with the sign-in screen (the code entry moved behind it).
    expect(ru("Agree and continue")).toBe("Принять и продолжить");
  });

  test("the location chip is short enough for its one line", () => {
    expect(ru("Location off · Sample area")).toBe("Нет геолокации · пример");
    expect(ru("Location off · Sample area").length).toBeLessThanOrEqual("Location off · Sample area".length);
  });
});
