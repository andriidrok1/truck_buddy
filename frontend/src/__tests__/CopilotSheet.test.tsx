import { fireEvent, render, screen } from "@testing-library/react-native";
import { Keyboard } from "react-native";

import { CopilotSheet } from "../components/CopilotSheet";

// Reanimated's native worklets are not installed under jest: plain views, no-op animations.
jest.mock("react-native-reanimated", () => {
  const { View, Text } = jest.requireActual<typeof import("react-native")>("react-native");
  return {
    __esModule: true,
    default: { View, Text },
    useSharedValue: (v: unknown) => {
      const sv = { value: v, get: () => sv.value, set: (n: unknown) => void (sv.value = n) };
      return sv;
    },
    useAnimatedStyle: (f: () => object) => f(),
    withSpring: (v: unknown) => v,
    runOnJS: (f: (...a: unknown[]) => void) => f,
  };
});
// The pan gesture needs the native handler module; the detector just renders its child.
jest.mock("react-native-gesture-handler", () => ({
  GestureDetector: ({ children }: { children: React.ReactNode }) => children,
  usePanGesture: () => ({}),
}));
// keyboard-controller is a native module too; the sticky view becomes a plain view.
jest.mock("../keyboard", () => {
  const { View } = jest.requireActual<typeof import("react-native")>("react-native");
  return { KeyboardStickyView: View };
});
// Icon font, avatar image and waveform animation all need native modules.
jest.mock("../components/Icon", () => {
  const { Text } = jest.requireActual<typeof import("react-native")>("react-native");
  return { Icon: ({ name }: { name: string }) => <Text>{`icon:${name}`}</Text> };
});
jest.mock("../components/Avatar", () => {
  const { View } = jest.requireActual<typeof import("react-native")>("react-native");
  return { Avatar: () => <View testID="avatar" /> };
});
// The parking card pulls in react-native-maps (native); it only shows on a route anyway.
jest.mock("../components/ParkingStatus", () => ({ ParkingStatus: () => null }));
jest.mock("../components/Waveform", () => {
  const { View } = jest.requireActual<typeof import("react-native")>("react-native");
  return { Waveform: () => <View /> };
});

const noop = () => {};
const sheet = (onSubmitText = noop) => (
  <CopilotSheet
    phase="ready"
    speaking={false}
    onShowOptions={noop}
    options={[]}
    parking={{}}
    onOptionParking={noop}
    onOptionRoute={noop}
    active={null}
    driving={false}
    remaining={null}
    onGo={noop}
    bottomInset={0}
    onAvatarPress={noop}
    onSubmitText={onSubmitText}
    onClose={noop}
    onEndRoute={noop}
    onLayout={noop}
  />
);

// RNTL 14: render() and fireEvent.* are async.
describe("CopilotSheet text mode", () => {
  const dismiss = jest.spyOn(Keyboard, "dismiss").mockImplementation(() => {});
  beforeEach(() => dismiss.mockClear());

  test("Type opens the input row; Done closes it and the keyboard", async () => {
    await render(sheet());
    expect(screen.queryByTestId("copilot-text-input")).toBeNull();
    await fireEvent.press(screen.getByTestId("type-button"));
    expect(screen.getByTestId("copilot-text-input")).toBeTruthy();
    expect(screen.queryByTestId("type-button")).toBeNull();
    await fireEvent.press(screen.getByTestId("copilot-text-close"));
    expect(dismiss).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId("copilot-text-input")).toBeNull();
    expect(screen.getByTestId("type-button")).toBeTruthy();
  });

  test("losing focus with an empty field leaves text mode; a draft keeps it", async () => {
    await render(sheet());
    await fireEvent.press(screen.getByTestId("type-button"));
    await fireEvent.changeText(screen.getByTestId("copilot-text-input"), "fuel near me");
    await fireEvent(screen.getByTestId("copilot-text-input"), "blur");
    expect(screen.getByTestId("copilot-text-input")).toBeTruthy();
    await fireEvent.changeText(screen.getByTestId("copilot-text-input"), "  ");
    await fireEvent(screen.getByTestId("copilot-text-input"), "blur");
    expect(screen.queryByTestId("copilot-text-input")).toBeNull();
  });

  test("send submits the trimmed draft and closes the row", async () => {
    const onSubmitText = jest.fn();
    await render(sheet(onSubmitText));
    await fireEvent.press(screen.getByTestId("type-button"));
    await fireEvent.changeText(screen.getByTestId("copilot-text-input"), "  Take me to Reno ");
    await fireEvent(screen.getByTestId("copilot-text-input"), "submitEditing");
    expect(onSubmitText).toHaveBeenCalledWith("Take me to Reno");
    expect(screen.queryByTestId("copilot-text-input")).toBeNull();
  });

  test("the grabber is there to drag", async () => {
    await render(sheet());
    expect(screen.getByTestId("sheet-grabber")).toBeTruthy();
    expect(screen.getByTestId("sheet-body")).toBeTruthy();
  });
});
