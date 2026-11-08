import { fireEvent, render, screen } from "@testing-library/react-native";
import * as WebBrowser from "expo-web-browser";

import { LegalLinks } from "../components/LegalLinks";
import { PRIVACY_URL, TERMS_URL } from "../constants";
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

jest.mock("expo-web-browser", () => ({ openBrowserAsync: jest.fn(() => Promise.resolve({ type: "opened" })) }));

// RNTL 14: render() and fireEvent.* are async.
describe("LegalLinks (truck profile)", () => {
  beforeEach(() => {
    jest.mocked(WebBrowser.openBrowserAsync).mockClear();
    useLangStore.setState({ lang: "en" });
  });

  test("shows both links with their testIDs", async () => {
    await render(<LegalLinks />);
    expect(screen.getByTestId("profile-privacy")).toBeTruthy();
    expect(screen.getByTestId("profile-terms")).toBeTruthy();
    expect(screen.getByText("Privacy policy")).toBeTruthy();
    expect(screen.getByText("Terms of use")).toBeTruthy();
  });

  test("each link opens its page in the in-app browser", async () => {
    await render(<LegalLinks />);
    await fireEvent.press(screen.getByTestId("profile-privacy"));
    expect(WebBrowser.openBrowserAsync).toHaveBeenCalledWith(PRIVACY_URL);
    await fireEvent.press(screen.getByTestId("profile-terms"));
    expect(WebBrowser.openBrowserAsync).toHaveBeenLastCalledWith(TERMS_URL);
    expect(WebBrowser.openBrowserAsync).toHaveBeenCalledTimes(2);
  });

  test("labels are translated for a Russian driver", async () => {
    useLangStore.setState({ lang: "ru" });
    await render(<LegalLinks />);
    expect(screen.getByText("Политика конфиденциальности")).toBeTruthy();
    expect(screen.getByText("Условия использования")).toBeTruthy();
  });
});
