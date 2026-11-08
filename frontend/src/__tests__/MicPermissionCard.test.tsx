// App Review 5.1.1(iv): the card in front of the iOS microphone prompt has one neutral "Continue" that always shows
// the prompt; no "Allow" wording and no "Not now" before the system has asked. Once the OS stopped asking, the card
// sends the driver to Settings and can be dismissed.
import { fireEvent, render, screen } from "@testing-library/react-native";
import { Platform } from "react-native";

import { MicPermissionCard } from "../components/MicPermissionCard";

jest.mock("react-native-reanimated", () => {
  const { View } = jest.requireActual<typeof import("react-native")>("react-native");
  return {
    __esModule: true,
    default: { View },
    FadeInDown: { duration: () => undefined },
    FadeOutDown: { duration: () => undefined },
    useSharedValue: (v: unknown) => {
      const sv = { value: v, get: () => sv.value, set: (n: unknown) => void (sv.value = n) };
      return sv;
    },
    useAnimatedStyle: (f: () => object) => f(),
    withSpring: (v: unknown) => v,
  };
});

const card = (state: "ask" | "blocked", onAllow = jest.fn(), onDismiss = jest.fn()) => (
  <MicPermissionCard state={state} bottom={0} onAllow={onAllow} onDismiss={onDismiss} />
);

describe("MicPermissionCard", () => {
  const os = Platform.OS;
  afterEach(() => {
    Platform.OS = os;
  });

  test("iOS, before the prompt: only Continue, and it asks", async () => {
    Platform.OS = "ios";
    const onAllow = jest.fn();
    await render(card("ask", onAllow));
    expect(screen.queryByTestId("mic-permission-dismiss-button")).toBeNull();
    expect(screen.queryByText("Allow microphone")).toBeNull();
    await fireEvent.press(screen.getByText("Continue"));
    expect(onAllow).toHaveBeenCalledTimes(1);
  });

  test("iOS, after a denial: Open Settings and Not now", async () => {
    Platform.OS = "ios";
    const onDismiss = jest.fn();
    await render(card("blocked", jest.fn(), onDismiss));
    expect(screen.getByText("Open Settings")).toBeTruthy();
    await fireEvent.press(screen.getByTestId("mic-permission-dismiss-button"));
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });

  test("Android keeps Not now before the prompt", async () => {
    Platform.OS = "android";
    await render(card("ask"));
    expect(screen.getByTestId("mic-permission-dismiss-button")).toBeTruthy();
    expect(screen.getByText("Continue")).toBeTruthy();
  });
});
