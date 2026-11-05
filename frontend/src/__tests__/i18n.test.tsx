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
    expect(t("In 500 feet, поверни налево на El Centro Rd.", undefined, "ru")).toBe("Через 500 футов, поверни налево на El Centro Rd.");
    expect(t("In 1.2 miles, keep right.", undefined, "ru")).toBe("Через 1.2 мили, keep right.");
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
