// "Where to?" (TopBar search): what the typed text becomes before it goes to the copilot. QA 2026-11-11: in Russian
// "найди дизель" was sent as "Take me to найди дизель".
import { useLangStore } from "../i18n";
import { searchRequest } from "../utils/searchRequest";

describe("searchRequest", () => {
  test("English: a bare place is wrapped, a request goes as typed (unchanged)", () => {
    expect(searchRequest("Reno", "en")).toBe("Take me to Reno");
    expect(searchRequest("1200 Main St, Sacramento", "en")).toBe("Take me to 1200 Main St, Sacramento");
    expect(searchRequest("I need fuel", "en")).toBe("I need fuel");
    expect(searchRequest("find a truck stop", "en")).toBe("find a truck stop");
    expect(searchRequest("Diesel", "en")).toBe("Diesel");
    // Russian words mean nothing to the English path: as before.
    expect(searchRequest("найди дизель", "en")).toBe("Take me to найди дизель");
  });

  test("Russian: a bare place becomes a Russian route request", () => {
    expect(searchRequest("Рино", "ru")).toBe("Маршрут до Рино");
    expect(searchRequest("Reno", "ru")).toBe("Маршрут до Reno");
    expect(searchRequest("Сакраменто, 1200 Main St", "ru")).toBe("Маршрут до Сакраменто, 1200 Main St");
    // A place that only starts like a short keyword is still a place.
    expect(searchRequest("Какабека", "ru")).toBe("Маршрут до Какабека");
  });

  test("Russian: a full request goes as typed, whatever the case or the word's position", () => {
    for (const q of [
      "найди дизель",
      "Найди дизель",
      "НАЙДИ ДИЗЕЛЬ",
      "мне нужна заправка",
      "нужен мотель",
      "где встать на ночь",
      "покажи стоянки рядом",
      "хочу в отель",
      "отвези в Рино",
      "маршрут до Рино",
      "поехали в Рино",
      "Дизель",
      "топливо",
      "парковка для трака",
      "весы",
      "мойка траков",
      "погода в Рино",
      "у меня поломка",
      "трак-стоп",
      "find diesel",
    ]) {
      expect(searchRequest(q, "ru")).toBe(q);
    }
  });

  test("the language comes from the app when it is not passed; a Ukrainian phone stays on the English path", () => {
    const before = useLangStore.getState().lang;
    useLangStore.setState({ lang: "ru" });
    expect(searchRequest("Рино")).toBe("Маршрут до Рино");
    useLangStore.setState({ lang: "en" });
    expect(searchRequest("Reno")).toBe("Take me to Reno");
    useLangStore.setState({ lang: before });
    expect(searchRequest("Рено", "uk")).toBe("Take me to Рено");
  });
});
