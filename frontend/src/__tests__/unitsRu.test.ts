// Units in Russian (QA 2026-11-05, M-4): the routes list said "8 min · 2.7 mi · current" and the nav banner "200 ft"
// in Russian mode. Every line here is what a screen draws; English must come out unchanged.
import { chipAhead } from "../geo/advisories";
import { formatDistance } from "../geo/guidance";
import { t } from "../i18n";

const ru = (en: string) => t(en, undefined, "ru");
const en = (s: string) => t(s, undefined, "en");

test("nav banner distances: feet and miles with the Russian plural", () => {
  expect(formatDistance(61)).toBe("200 ft");
  expect(ru(formatDistance(61))).toBe("200 футов");
  expect(ru(formatDistance(152))).toBe("500 футов");
  expect(ru("50 ft")).toBe("50 футов");
  expect(ru("1 ft")).toBe("1 фут");
  expect(ru("2 ft")).toBe("2 фута");
  expect(ru(formatDistance(644))).toBe("0.4 мили");
  expect(ru("21 mi")).toBe("21 миля");
  expect(ru("12 mi")).toBe("12 миль");
});

test("route facts: duration, distance, tolls and the current mark, fact by fact", () => {
  expect(ru("8 min · 2.7 mi · current")).toBe("8 мин · 2.7 мили · текущий");
  expect(ru("2 h 54 min · 135 mi · current")).toBe("2 ч 54 мин · 135 миль · текущий");
  expect(ru("8 min · 4.2 mi · $12.50 tolls")).toBe("8 мин · 4.2 мили · $12.50 за платные дороги");
  expect(ru("45 mph")).toBe("45 миль/ч");
});

test("advisory chips, the off-route line and the truck summary", () => {
  expect(ru(chipAhead(4828))).toBe("через 3 мили");
  expect(ru(chipAhead(1609))).toBe("через 1 милю");
  expect(ru("Return to the route: 0.4 mi NE")).toBe("Вернись на маршрут: 0.4 мили к северо-востоку");
  expect(ru("14 ft 0 in · 53 ft trailer · 80,000 lb · HAZMAT")).toBe("14 фт 0 дюйм. · трейлер 53 фута · 80,000 фунтов · HAZMAT");
  expect(ru("Height must be 10 ft to 15 ft.")).toBe("Высота должна быть от 10 футов до 15 футов.");
  expect(ru("12 min by reviews")).toBe("12 мин по отзывам");
});

test("a fact line keeps what has no translation (a place name) and English is untouched", () => {
  expect(ru("11 min · 4.5 mi · Pilot Travel Center")).toBe("11 мин · 4.5 мили · Pilot Travel Center");
  for (const s of ["8 min · 2.7 mi · current", "200 ft", "in 3 mi", "Return to the route: 0.4 mi NE", "14 ft 0 in · 53 ft trailer · 80,000 lb"]) {
    expect(en(s)).toBe(s);
  }
});


test("number slots match numbers only: free text that ends in a unit is left alone, a real measure gets its plural", () => {
  expect(ru("13 ft")).toBe("13 футов");
  expect(ru("Low clearance 13 ft")).toBe("Low clearance 13 ft"); // not "Low clearance 13 фута"
  expect(ru("Love's Travel Stop · Exit 12 · Open 24 h")).toBe("Love's Travel Stop · Exit 12 · Open 24 h");
  expect(ru("Navigating to 76 Truck Stop")).toBe("Едем в 76 Truck Stop");
  expect(ru("Routes (3)")).toBe("Маршруты (3)");
  expect(ru("$12.50 tolls")).toBe("$12.50 за платные дороги");
  expect(ru("80,000 lb")).toBe("80,000 фунтов");
  expect(ru("2 h 54 min · 135 mi")).toBe("2 ч 54 мин · 135 миль");
  for (const s of ["Low clearance 13 ft", "Love's Travel Stop · Exit 12 · Open 24 h", "Navigating to 76 Truck Stop"]) {
    expect(en(s)).toBe(s);
  }
});
