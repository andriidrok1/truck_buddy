import { render, screen } from "@testing-library/react-native";

import { pluralRu, setLang, t, useLangStore } from "../i18n";
import { Text } from "../i18n/Text";
import { RU } from "../i18n/ru";

afterEach(() => useLangStore.setState({ lang: "en" }));

describe("i18n", () => {
  test("English passes through; Russian by exact key", () => {
    expect(t("Tap to talk", undefined, "en")).toBe("Tap to talk");
    expect(t("Tap to talk", undefined, "ru")).toBe("Нажми и говори");
    expect(t("Pilot Travel Center", undefined, "ru")).toBe("Pilot Travel Center"); // no entry: unchanged
  });

  test("patterns move values over and put nouns in their form", () => {
    expect(t("In 500 feet, поверни налево на El Centro Rd.", undefined, "ru")).toBe("Через 500 футов поверни налево на El Centro Rd.");
    // The slot's value moves over as it is; guidance.ts no longer builds such a line in Russian (QA 2026-11-11).
    expect(t("In 1.2 miles, keep right onto I-80.", undefined, "ru")).toBe("Через 1,2 мили keep right onto I-80.");
    expect(t("In 1 miles", undefined, "ru")).toBe("Через 1 милю");
    expect(t("Navigating to Reno, NV", undefined, "ru")).toBe("Едем в Reno, NV");
    expect(t("End the route to {name}?", { name: "Reno" }, "ru")).toBe("Завершить маршрут до Reno?");
    expect([1, 2, 5, 11, 21, 22, 112].map((n) => pluralRu(n, "фут", "фута", "футов"))).toEqual(
      ["фут", "фута", "футов", "футов", "фут", "фута", "футов"]);
  });

  test("Ukrainian is never answered in Russian", () => {
    expect(t("Tap to talk", undefined, "uk")).toBe("Tap to talk");
  });

  test("every Russian entry keeps the placeholders of its key", () => {
    for (const [en, ru] of Object.entries(RU)) {
      const names = (s: string) => [...s.matchAll(/\{(\w+)(?:\|[^}]*)?\}/g)].map((m) => m[1]).sort();
      expect({ en, names: names(ru) }).toEqual({ en, names: names(en) });
    }
  });

  // RNTL 14: render() is async.
  test("Text shows its string in the driver's language and follows a change", async () => {
    await render(<Text testID="x">Tap to talk</Text>);
    expect(screen.getByTestId("x").props.children).toBe("Tap to talk");
    setLang("ru");
    await render(<Text testID="y">{"You said: “"}{"find parking"}{"”"}</Text>);
    expect(screen.getByTestId("y").props.children).toBe("Ты сказал: «find parking»");
  });
});

describe("i18n, review 2026-11-05", () => {
  test("a pattern slot may be empty", () => {
    expect(t("All set! Tap me anytime and tell me where you need to go.", undefined, "ru")).toBe(
      "Всё готово! Нажимай на меня в любой момент и говори, куда нужно.");
    expect(t("All set, Mike! Tap me anytime and tell me where you need to go.", undefined, "ru")).toBe(
      "Всё готово, Mike! Нажимай на меня в любой момент и говори, куда нужно.");
  });

  test("the saved language comes back after a restart", async () => {
    // A fresh module registry = a fresh app start: the choice is on disk before the i18n module loads.
    let fresh: typeof import("../i18n") | undefined;
    let store: typeof import("../utils/storage") | undefined;
    jest.isolateModules(() => {
      store = jest.requireActual<typeof import("../utils/storage")>("../utils/storage");
    });
    await store!.storage.setItem("lang_v1", "ru");
    jest.isolateModules(() => {
      jest.doMock("../utils/storage", () => store);
      fresh = jest.requireActual<typeof import("../i18n")>("../i18n");
    });
    // No explicit loadLang(): importing the module must be enough (no screen calls a hook for it).
    for (let i = 0; i < 5; i++) await new Promise((r) => setTimeout(r, 0));
    expect(fresh!.getLang()).toBe("ru");
  });
});

test("the break reminder, with and without a name", () => {
  expect(t("Hey Mike, you've been driving for 3 hours. Want me to find a rest area?", undefined, "ru")).toBe("Mike, ты за рулём уже 3 часа. Найти рест-эрию?");
  expect(t("you've been driving for an hour. Want me to find a rest area?", undefined, "ru")).toBe("Ты за рулём уже час. Найти рест-эрию?");
  expect(t("No signal right now. I can still read your turns and what we loaded.", undefined, "ru")).toBe("Сейчас нет связи. Повороты и то, что загружено, я всё ещё вижу.");
});
