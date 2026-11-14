// Russian QA 2026-11-09 (vault: 40-Events/VibeCon-SF-2026/TruckBuddy — QA русской версии): numbers, clock,
// truck form errors and the spoken lines. English must come out exactly as before.
import { advisoryText, type Advisory } from "../geo/advisories";
import { parkingBadgeText } from "../geo/parkingBadge";
import { routeFacts, switchedLine } from "../geo/routeChoice";
import { formatNumber, groupedInput, numRu, t, useLangStore } from "../i18n";
import { truckErrors } from "../truck/validate";
import type { ParkingCheck, Place, RouteInfo } from "../types";
import { clockTime, numericDate, shortDate } from "../utils/arrivalClock";
import { nearbyLine, tipsLine, verdictLine } from "../voice/parkingLines";

const NB = " ";
const ru = (en: string) => t(en, undefined, "ru");
const en = (s: string) => t(s, undefined, "en");

describe("numbers (bug 6, 24, S21)", () => {
  test("Russian: decimal comma, thousands split by a no-break space; a bare four-digit number stays whole", () => {
    expect(numRu("4.5")).toBe("4,5");
    expect(numRu("80,000")).toBe(`80${NB}000`);
    expect(numRu("80000")).toBe(`80${NB}000`);
    expect(numRu("5,000")).toBe(`5${NB}000`);
    expect(numRu(1234567.25)).toBe(`1${NB}234${NB}567,25`);
    expect(numRu("$3.45")).toBe("$3,45");
    expect(numRu("2026")).toBe("2026");
    expect(numRu("Exit 522")).toBe("Exit 522");
    expect(formatNumber(4.5, "ru")).toBe("4,5");
    expect(formatNumber(80000, "ru")).toBe(`80${NB}000`);
    expect(formatNumber(6.5, "ru", 1)).toBe("6,5");
  });

  test("English keeps the point and the comma", () => {
    expect(formatNumber(4.5, "en")).toBe("4.5");
    expect(formatNumber(80000, "en")).toBe("80,000");
    for (const s of ["4.5 mi", "80,000 lb", "In 0.2 miles, turn right on L St.", "8 min · 4.2 mi · $12.50 tolls"]) expect(en(s)).toBe(s);
  });

  test("every place a number is filled in Russian: units, turns, the route line, values passed in", () => {
    expect(ru("4.5 mi")).toBe("4,5 мили");
    expect(ru("0.2 mi")).toBe("0,2 мили");
    expect(ru("In 0.2 miles, turn right on L St.")).toBe("Через 0,2 мили turn right on L St."); // bug 15: no comma
    expect(ru("Fastest · 2 h 48 min · 133 mi · current")).toBe("Быстрее всего · 2 ч 48 мин · 133 мили · текущий"); // bug 4
    expect(t("Diesel {price}", { price: "$3.45" }, "ru")).toBe("Дизель $3,45");
    expect(ru("12.5 MB")).toBe("12,5 МБ");
  });

  test("the weight field shows 80 000 in Russian and stays as typed in English", () => {
    expect(groupedInput("80000", "ru")).toBe(`80${NB}000`);
    expect(groupedInput(`80${NB}0000`, "ru")).toBe(`800${NB}000`);
    expect(groupedInput("", "ru")).toBe("");
    expect(groupedInput("80000", "en")).toBe("80000");
  });

  test("a route's facts with the cheapest tag in both languages", () => {
    const r = { duration_s: 480, distance_mi: 4.2, tolls_usd: 12.5, tags: ["fastest"] } as unknown as RouteInfo;
    expect(routeFacts(r)).toBe("Fastest · 8 min · 4.2 mi · $12.50 tolls");
    expect(ru(routeFacts(r))).toBe("Быстрее всего · 8 мин · 4,2 мили · $12,50 за платные дороги");
  });
});

describe("clock and dates follow the app's language, not the phone (bug 10, S9, S16)", () => {
  const at = new Date(2026, 10, 9, 23, 40);
  test("24-hour in Russian, the phone's format in English", () => {
    expect(clockTime(at, "ru")).toBe("23:40");
    expect(clockTime(new Date(2026, 10, 9, 7, 5), "ru")).toBe("7:05");
    expect(clockTime(at, "en")).toBe(at.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }));
  });
  test("day separators and download dates", () => {
    expect(shortDate(new Date(2026, 9, 28), "ru")).toBe("28 окт.");
    expect(shortDate(new Date(2026, 9, 28), "en")).toBe(new Date(2026, 9, 28).toLocaleDateString([], { month: "short", day: "numeric" }));
    expect(numericDate(new Date(2026, 9, 8), "ru")).toBe("08.10.2026");
  });
  test("the live parking badge: Russian words and a 24-hour 'as of'", () => {
    const NOW = new Date(2026, 9, 27, 20, 0).getTime();
    const place = { id: "p", name: "Pilot", category: "truck_stop", lat: 0, lng: 0, tags: [], parking_available: 12, parking_capacity: 40, parking_low: true, parking_updated: new Date(2026, 9, 27, 19, 2).toISOString() } as Place;
    expect(parkingBadgeText(place, NOW, "ru")).toMatchObject({ text: "свободно 12 из 40 · почти всё занято", asOf: "на 19:02" });
    expect(parkingBadgeText(place, NOW, "en")).toMatchObject({ text: "12/40 open · almost full" });
  });
});

describe("truck form errors (bug 2, S20, S21)", () => {
  const ok = { feet: "13", inches: "6", trailer: "53", weight: "80000" };
  const all = truckErrors({ ...ok, feet: "18", weight: "800000" });

  test("each message translates on its own, with Russian numbers and units", () => {
    expect(ru(all.height!)).toBe("Высота должна быть от 5 футов до 16 футов 11 дюймов.");
    expect(ru(all.weight!)).toBe(`Полный вес должен быть от 5${NB}000 до 200${NB}000 фунтов.`);
    expect(ru(truckErrors({ ...ok, trailer: "80" }).trailer!)).toBe("Трейлер должен быть от 10 до 65 футов.");
  });

  test("two messages glued together are two Russian sentences: the first slot never swallows the second", () => {
    const glued = `${all.height} ${all.weight}`;
    expect(ru(glued)).toBe(`Высота должна быть от 5 футов до 16 футов 11 дюймов. Полный вес должен быть от 5${NB}000 до 200${NB}000 фунтов.`);
    // A slot that holds a second sentence the dictionary doesn't know still matches as before.
    expect(ru("Heads up: Crash on I-80. Expect delays")).toBe("Внимание: Crash on I-80. Expect delays");
  });

  test("English is unchanged", () => {
    expect(all.height).toBe("Height must be 5 ft to 16 ft 11 in.");
    expect(en(all.weight!)).toBe("Gross weight must be 5,000 to 200,000 lb.");
  });
});

describe("spoken lines are whole Russian sentences (S1–S5, S10, S11, S15)", () => {
  const adv = (over: Partial<Advisory>): Advisory => ({ id: "x", kind: "incident", text: "", severity: 2, aheadM: 800, index: 5, ...over });

  test("advisories", () => {
    // An English description is no longer read out in Russian (QA 2026-11-11, src/__tests__/ruQa1111.test.ts).
    expect(advisoryText(adv({ severity: 3, text: "Road closed: US-50 at Echo Summit" }), "ru")).toBe("Внимание: дорога закрыта через полмили.");
    expect(advisoryText(adv({ text: "Accident: crash on I-80" }), "ru")).toBe("Внимание: авария через полмили.");
    expect(advisoryText(adv({ kind: "chain", text: "Chains R-2 on US-50, Twin Bridges", aheadM: 6400 }), "ru")).toBe(
      "Нужны цепи примерно через 4 мили: R-2 на US-50, Twin Bridges.");
    expect(advisoryText(adv({ text: "Accident: crash on I-80" }), "en")).toBe("Heads up: Accident in half a mile: crash on I-80.");
    expect(ru("now")).toBe("сейчас");
  });

  test("parking verdict, nearby lot and the entry tip", () => {
    const pk = { verdict: "unlikely", nearby: { direction: "southeast", kind: "private yard" } } as ParkingCheck;
    expect(t("Heads up: from the satellite, parking there looks tight for a rig.", undefined, "ru")).toBe("Внимание: по спутнику парковка там тесная для трака.");
    expect(verdictLine(pk)).toBe("Heads up: from the satellite, parking there looks tight for a rig. There's a lot with trailers to the southeast, looks like a private yard.");
    expect(nearbyLine({ ...pk, nearby: { direction: "west", kind: "unclear" } })).toBe(" There's a lot with trailers to the west.");
    expect(tipsLine({ verdict: "likely", entry_tip: "On the north side", park_tip: "Back row", relative: true } as ParkingCheck)).toBe(
      "The truck entrance is on the north side. Back row.");
    useLangStore.setState({ lang: "ru" });
    try {
      expect(verdictLine(pk)).toBe("Внимание: по спутнику парковка там тесная для трака. Есть площадка с трейлерами к юго-востоку, похоже, частная площадка.");
      expect(verdictLine({ verdict: "likely" } as ParkingCheck)).toBe("С парковкой там вроде нормально.");
      expect(tipsLine({ verdict: "likely", entry_tip: "С северной стороны", park_tip: "Задний ряд", relative: true } as ParkingCheck)).toBe(
        "Въезд для траков с северной стороны. Задний ряд.");
    } finally {
      useLangStore.setState({ lang: "en" });
    }
  });

  test("switching routes names each label in the driver's language", () => {
    const r = { labels: ["Fastest ETA", "Cheapest"] } as unknown as RouteInfo;
    expect(switchedLine(r, 0, "ru")).toBe("Переключил на маршрут Быстрее всего и Дешевле всего.");
    expect(switchedLine(r, 0, "en")).toBe("Switched to the Fastest ETA and Cheapest route.");
    expect(switchedLine({} as RouteInfo, 1, "ru")).toBe("Переключил на вариант 2.");
  });
});
