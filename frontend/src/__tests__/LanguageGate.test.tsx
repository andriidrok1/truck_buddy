import { fireEvent, render, screen } from "@testing-library/react-native";

import { LanguageGate } from "../components/LanguageGate";
import { LANG_KEY, useLangStore } from "../i18n";
import { storage } from "../utils/storage";

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
    withTiming: (v: unknown) => v,
  };
});
jest.mock("react-native-safe-area-context", () => ({ useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }) }));
jest.mock("../components/Icon", () => {
  const { Text } = jest.requireActual<typeof import("react-native")>("react-native");
  return { Icon: ({ name }: { name: string }) => <Text>{`icon:${name}`}</Text> };
});

beforeEach(async () => {
  await storage.removeItem(LANG_KEY);
  useLangStore.setState({ lang: "en", chosen: false, loaded: true });
});

const checked = (code: string) => screen.getByTestId(`language-${code}`).props.accessibilityState?.checked;

// RNTL 14: render() and fireEvent.* are async.
describe("LanguageGate", () => {
  test("asks in both languages and offers English and Russian, not Ukrainian until it is ready", async () => {
    await render(<LanguageGate />);
    expect(screen.getByText("Choose your language\nВыбери язык")).toBeTruthy();
    expect(screen.getByTestId("language-en")).toBeTruthy();
    expect(screen.getByTestId("language-ru")).toBeTruthy();
    expect(screen.queryByTestId("language-uk")).toBeNull();
  });

  test("the phone's language is picked already: an English phone", async () => {
    await render(<LanguageGate />);
    expect(checked("en")).toBe(true);
    expect(checked("ru")).toBe(false);
    expect(screen.getByTestId("language-en-check")).toBeTruthy();
    expect(screen.queryByTestId("language-ru-check")).toBeNull();
    expect(screen.getByText("Continue")).toBeTruthy();
  });

  test("the phone's language is picked already: a Russian phone, with the button in Russian", async () => {
    useLangStore.setState({ lang: "ru" }); // what deviceLang() gives the store on a Russian phone
    await render(<LanguageGate />);
    expect(checked("ru")).toBe(true);
    expect(checked("en")).toBe(false);
    expect(screen.getByText("Продолжить")).toBeTruthy();
  });

  test("a tap moves the mark and nothing else: no choice saved, the app stays on this screen", async () => {
    await render(<LanguageGate />);
    await fireEvent.press(screen.getByTestId("language-ru"));
    expect(checked("ru")).toBe(true);
    expect(checked("en")).toBe(false);
    expect(screen.getByTestId("language-ru-check")).toBeTruthy();
    expect(screen.getByText("Продолжить")).toBeTruthy(); // the button already reads in the language picked
    expect(useLangStore.getState()).toMatchObject({ lang: "en", chosen: false });
    expect(await storage.getItem(LANG_KEY, null)).toBeNull();
  });

  test("a Russian phone, the driver taps English: the button reads Continue, not Продолжить (review of #169)", async () => {
    useLangStore.setState({ lang: "ru" });
    await render(<LanguageGate />);
    expect(screen.getByText("Продолжить")).toBeTruthy();
    await fireEvent.press(screen.getByTestId("language-en"));
    expect(checked("en")).toBe(true);
    expect(screen.getByText("Continue")).toBeTruthy();
    expect(screen.queryByText("Продолжить")).toBeNull();
    expect(screen.getByTestId("language-continue").props.accessibilityLabel).toBe("Continue");
    expect(useLangStore.getState()).toMatchObject({ lang: "ru", chosen: false });
    await fireEvent.press(screen.getByTestId("language-ru"));
    expect(screen.getByText("Продолжить")).toBeTruthy();
    expect(screen.getByTestId("language-continue").props.accessibilityLabel).toBe("Продолжить");
  });

  test("Continue saves the language picked and lets the app go on", async () => {
    await render(<LanguageGate />);
    await fireEvent.press(screen.getByTestId("language-ru"));
    await fireEvent.press(screen.getByTestId("language-continue"));
    expect(useLangStore.getState()).toMatchObject({ lang: "ru", chosen: true });
    expect(await storage.getItem(LANG_KEY, null)).toBe("ru");
  });

  test("Continue without a tap keeps the phone's language", async () => {
    await render(<LanguageGate />);
    await fireEvent.press(screen.getByTestId("language-continue"));
    expect(useLangStore.getState()).toMatchObject({ lang: "en", chosen: true });
  });
});
