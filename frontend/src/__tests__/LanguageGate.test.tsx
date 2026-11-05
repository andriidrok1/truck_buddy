import { fireEvent, render, screen } from "@testing-library/react-native";

import { LanguageGate } from "../components/LanguageGate";
import { useLangStore } from "../i18n";

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

beforeEach(() => useLangStore.setState({ lang: "en", chosen: false, loaded: true }));

// RNTL 14: render() and fireEvent.* are async.
describe("LanguageGate", () => {
  test("asks in both languages and offers English and Russian, not Ukrainian until it is ready", async () => {
    await render(<LanguageGate />);
    expect(screen.getByText("Choose your language\nВыберите язык")).toBeTruthy();
    expect(screen.getByTestId("language-en")).toBeTruthy();
    expect(screen.getByTestId("language-ru")).toBeTruthy();
    expect(screen.queryByTestId("language-uk")).toBeNull();
  });

  test("a tap picks the language and lets the app go on", async () => {
    await render(<LanguageGate />);
    await fireEvent.press(screen.getByTestId("language-ru"));
    expect(useLangStore.getState()).toMatchObject({ lang: "ru", chosen: true });
  });
});
