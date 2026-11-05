// UI review 2026-11-05, second pass (N6): the red End route in the confirmation sat right where the round End
// route button had been, so a double tap ended the route through the question.
import { act, fireEvent, render, screen } from "@testing-library/react-native";

import { ARM_MS, ConfirmSheet } from "../components/ConfirmSheet";

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
jest.mock("react-native-safe-area-context", () => ({ useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }) }));

const sheet = (onConfirm: () => void, onCancel: () => void) => (
  <ConfirmSheet testID="end-route-confirm" title="End the route to Reno?" confirmLabel="End route" cancelLabel="Keep driving" onConfirm={onConfirm} onCancel={onCancel} />
);

describe("ConfirmSheet", () => {
  afterEach(() => jest.useRealTimers());

  test("the safe answer comes first (on top, where the finger was), the destructive one under it", async () => {
    await render(sheet(() => {}, () => {}));
    const ids = screen.getByTestId("end-route-confirm").children.map((c) => (typeof c === "string" ? c : c.props.testID));
    expect(ids.indexOf("end-route-confirm-cancel")).toBeGreaterThanOrEqual(0);
    expect(ids.indexOf("end-route-confirm-cancel")).toBeLessThan(ids.indexOf("end-route-confirm-confirm"));
  });

  test("a tap on End route right after the sheet opens is ignored; a later one ends the route", async () => {
    jest.useFakeTimers();
    const onConfirm = jest.fn();
    await render(sheet(onConfirm, () => {}));
    await fireEvent.press(screen.getByTestId("end-route-confirm-confirm"));
    expect(onConfirm).not.toHaveBeenCalled();
    await act(async () => {
      jest.advanceTimersByTime(ARM_MS + 50);
    });
    await fireEvent.press(screen.getByTestId("end-route-confirm-confirm"));
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  test("Keep driving answers at once", async () => {
    const onCancel = jest.fn();
    await render(sheet(() => {}, onCancel));
    await fireEvent.press(screen.getByTestId("end-route-confirm-cancel"));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });
});
