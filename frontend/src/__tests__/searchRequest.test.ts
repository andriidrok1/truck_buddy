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

  // Still a place, although it starts like a keyword (QA review 2026-11-11).
  test.each([
    "Рино", "Reno", "Pilot Reno", "Love's", "1200 Main St, Sacramento", "Сакраменто, 1200 Main St",
    "Парковая улица", "Весёлое", "Гдов", "Парк Сити", "Якутск", "Каково", "Какабека", "Душанбе", "Естественная улица",
  ])("Russian: the bare place %p becomes a route request", (q) => {
    expect(searchRequest(q, "ru")).toBe(`Маршрут до ${q}`);
  });

  test.each([
    // what the plan listed
    "найди дизель", "Найди дизель", "НАЙДИ ДИЗЕЛЬ", "мне нужна заправка", "нужен мотель", "где встать на ночь", "покажи стоянки рядом",
    "хочу в отель", "отвези в Рино", "маршрут до Рино", "поехали в Рино", "Дизель", "топливо", "парковка для трака", "весы", "мойка траков",
    "погода в Рино", "у меня поломка", "трак-стоп",
    // everyday requests that were wrapped as a destination
    "душ", "кофе", "туалет", "еда", "поесть", "я устал", "когда приеду", "что впереди", "есть ли пробки", "отмени", "едем в Рино", "домой",
    "трак стоп", "тракстоп", "автозаправка", "АЗС", "автостоянка", "спать", "переночевать", "ночлег", "мне надо поспать", "нужно в душ",
    "какая погода", "сколько ехать", "останови маршрут", "как проехать",
    // English typed in Russian mode, and a name that is a request by its first word
    "find diesel", "Мотель 6",
  ])("Russian: the request %p goes as typed", (q) => {
    expect(searchRequest(q, "ru")).toBe(q);
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
