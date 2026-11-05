// UI review 2026-11-05: the Russian texts that were left English, the tag line, the Cyrillic font, the mic
// request that gave up too early, and the history screen's empty and error states.
import { act, fireEvent, render, screen } from "@testing-library/react-native";
import { StyleSheet } from "react-native";

import { t, useLangStore } from "../i18n";
import { Text } from "../i18n/Text";
import { fontFor, fonts } from "../theme";
import { formatUsPhone, placeTagLine } from "../utils/placeTags";
import { askWithDeadline } from "../voice/micAsk";
import { HISTORY_EMPTY, HISTORY_ERROR, HistoryEmpty } from "../components/HistoryEmpty";

jest.mock("../components/Icon", () => {
  const { Text: RNText } = jest.requireActual<typeof import("react-native")>("react-native");
  return { Icon: ({ name }: { name: string }) => <RNText>{`icon:${name}`}</RNText> };
});
jest.mock("react-native-reanimated", () => {
  const { View } = jest.requireActual<typeof import("react-native")>("react-native");
  return {
    __esModule: true,
    default: { View },
    useSharedValue: (v: unknown) => {
      const sv = { value: v, get: () => sv.value, set: (n: unknown) => void (sv.value = n) };
      return sv;
    },
    useAnimatedStyle: (f: () => object) => f(),
    withSpring: (v: unknown) => v,
  };
});

afterEach(() => useLangStore.setState({ lang: "en" }));

describe("Russian route sheet and options", () => {
  test("the route summary and distances read in Russian units", () => {
    expect(t("11 min · 4.5 mi", undefined, "ru")).toBe("11 мин · 4.5 мили");
    expect(t("1 h 5 min · 62 mi", undefined, "ru")).toBe("1 ч 5 мин · 62 мили");
    expect(t("2 h · 121 mi", undefined, "ru")).toBe("2 ч · 121 миля");
    expect(t("4.0 mi", undefined, "ru")).toBe("4.0 мили");
    expect(t("25 min", undefined, "ru")).toBe("25 мин");
    expect(t("12 mph", undefined, "ru")).toBe("12 миль/ч");
  });

  test("counts in the options header and the route card", () => {
    expect(t("4 places nearby", undefined, "ru")).toBe("Рядом 4 места");
    expect(t("5 places nearby", undefined, "ru")).toBe("Рядом 5 мест");
    expect(t("4 other places", undefined, "ru")).toBe("Ещё 4 места");
    expect(t("1 other place", undefined, "ru")).toBe("Ещё 1 место");
    expect(t("Routes (3)", undefined, "ru")).toBe("Маршруты (3)");
  });

  test("place tags are translated one by one and the phone is formatted", () => {
    const tags = ["DEF", "24/7", "7 truck spots", "9163722893"];
    expect(placeTagLine(tags, (s) => t(s, undefined, "ru"))).toBe("DEF · 24/7 · 7 мест для траков · (916) 372-2893");
    expect(placeTagLine(tags, (s) => t(s, undefined, "en"))).toBe("DEF · 24/7 · 7 truck spots · (916) 372-2893");
    expect(placeTagLine(["Truck parking", "21 truck spots"], (s) => t(s, undefined, "ru"))).toBe("Парковка для траков · 21 место для траков");
  });

  test("only a US number counts as a phone", () => {
    expect(formatUsPhone("+1 916-372-2893")).toBe("(916) 372-2893");
    expect(formatUsPhone("24/7")).toBeNull();
    expect(formatUsPhone("53 ft parking")).toBeNull();
    expect(formatUsPhone("12345")).toBeNull();
  });
});

describe("Cyrillic font", () => {
  test("every app weight maps to the same Manrope weight in Russian only", () => {
    expect(fontFor(fonts.bold, "ru")).toBe("Manrope-Bold");
    expect(fontFor(fonts.extrabold, "ru")).toBe("Manrope-ExtraBold");
    expect(fontFor(fonts.regular, "ru")).toBe("Manrope-Regular");
    expect(fontFor(fonts.bold, "en")).toBe(fonts.bold);
    expect(fontFor(undefined, "ru")).toBeUndefined();
  });

  test("Text swaps the family when the language is Russian and keeps the weight", async () => {
    await render(<Text testID="h" style={{ fontFamily: fonts.bold, fontSize: 22 }}>Ask your copilot</Text>);
    expect(StyleSheet.flatten(screen.getByTestId("h").props.style).fontFamily).toBe(fonts.bold);
    await act(async () => useLangStore.setState({ lang: "ru" }));
    const style = StyleSheet.flatten(screen.getByTestId("h").props.style);
    expect(style.fontFamily).toBe("Manrope-Bold");
    expect(style.fontSize).toBe(22);
  });
});

describe("mic request with a deadline", () => {
  beforeEach(() => jest.useFakeTimers());
  afterEach(() => jest.useRealTimers());

  test("a quick answer is not late and never shows Settings", async () => {
    const onSlow = jest.fn();
    const res = await askWithDeadline(() => Promise.resolve("granted"), 5000, onSlow, "blocked");
    expect(res).toEqual({ perm: "granted", late: false });
    jest.advanceTimersByTime(10_000);
    expect(onSlow).not.toHaveBeenCalled();
  });

  test("a slow grant still comes through after the card offered Settings", async () => {
    let grant: (p: string) => void = () => {};
    const onSlow = jest.fn();
    const pending = askWithDeadline(() => new Promise<string>((r) => (grant = r)), 5000, onSlow, "blocked");
    jest.advanceTimersByTime(5000);
    expect(onSlow).toHaveBeenCalledTimes(1);
    grant("granted");
    expect(await pending).toEqual({ perm: "granted", late: true });
  });

  test("a failed request counts as the fallback", async () => {
    const res = await askWithDeadline(() => Promise.reject(new Error("no")), 5000, () => {}, "blocked");
    expect(res.perm).toBe("blocked");
  });
});

describe("history empty and error states", () => {
  test("the error reads upright and has a Retry button", async () => {
    const onRetry = jest.fn();
    await render(<HistoryEmpty error onRetry={onRetry} />);
    expect(screen.getByTestId("history-empty-text").props.children).toBe(HISTORY_ERROR);
    // Not inside the inverted list any more: nothing on the way up flips it.
    expect(StyleSheet.flatten(screen.getByTestId("history-empty").props.style).transform).toBeUndefined();
    await fireEvent.press(screen.getByTestId("history-retry"));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  test("an empty memory has no Retry", async () => {
    await render(<HistoryEmpty error={false} onRetry={() => {}} />);
    expect(screen.getByTestId("history-empty-text").props.children).toBe(HISTORY_EMPTY);
    expect(screen.queryByTestId("history-retry")).toBeNull();
  });
});
