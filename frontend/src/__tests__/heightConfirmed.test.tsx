import { fireEvent, render, screen } from "@testing-library/react-native";

import { HeightConfirmCard } from "../components/HeightConfirmCard";
import {
  confirmHeight,
  HEIGHT_CONFIRMED_KEY,
  isHeightConfirmed,
  loadHeightConfirmed,
  resetHeightConfirmedForTests,
} from "../truck/heightConfirmed";
import { storage } from "../utils/storage";

jest.mock("react-native-reanimated", () => {
  const { View } = jest.requireActual<typeof import("react-native")>("react-native");
  return {
    __esModule: true,
    default: { View },
    FadeInDown: { duration: () => ({}) },
    FadeOutDown: { duration: () => ({}) },
    useSharedValue: (v: unknown) => {
      const sv = { value: v, get: () => sv.value, set: (n: unknown) => void (sv.value = n) };
      return sv;
    },
    useAnimatedStyle: (f: () => object) => f(),
    withSpring: (v: unknown) => v,
    withTiming: (v: unknown) => v,
  };
});
jest.mock("../components/Icon", () => {
  const { Text } = jest.requireActual<typeof import("react-native")>("react-native");
  return { Icon: ({ name }: { name: string }) => <Text>{`icon:${name}`}</Text> };
});

beforeEach(async () => {
  resetHeightConfirmedForTests();
  await storage.removeItem(HEIGHT_CONFIRMED_KEY);
});

describe("height confirmed", () => {
  test("a fresh install has not answered; a confirm sticks across restarts", async () => {
    expect(await loadHeightConfirmed()).toBe(false);
    expect(isHeightConfirmed()).toBe(false);
    await confirmHeight();
    expect(isHeightConfirmed()).toBe(true);
    resetHeightConfirmedForTests(); // the app restarts
    expect(await loadHeightConfirmed()).toBe(true);
  });

  test("a confirm during the first read is not undone by the read", async () => {
    const reading = loadHeightConfirmed();
    await confirmHeight();
    expect(await reading).toBe(true);
  });
});

// RNTL 14: render() and fireEvent.* are async.
describe("HeightConfirmCard", () => {
  test("one tap for 13'6\" or 14', Other for the rest, no way to dismiss", async () => {
    const onPick = jest.fn();
    const onOther = jest.fn();
    await render(<HeightConfirmCard bottom={0} current={162} onPick={onPick} onOther={onOther} />);
    expect(screen.getByText("How tall is your truck? I plan routes under bridges by it.")).toBeTruthy();
    await fireEvent.press(screen.getByTestId("height-confirm-168"));
    expect(onPick).toHaveBeenCalledWith(168);
    await fireEvent.press(screen.getByTestId("height-confirm-other"));
    expect(onOther).toHaveBeenCalled();
    expect(screen.queryByText("Not now")).toBeNull();
  });
});
