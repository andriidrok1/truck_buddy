import { fireEvent, render, screen, within } from "@testing-library/react-native";
import { Keyboard, StyleSheet } from "react-native";

import { CopilotSheet } from "../components/CopilotSheet";

// Reanimated's native worklets are not installed under jest: plain views, no-op animations.
jest.mock("react-native-reanimated", () => {
  const { View, Text, ScrollView } = jest.requireActual<typeof import("react-native")>("react-native");
  return {
    __esModule: true,
    default: { View, Text, ScrollView },
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

  test("the grabber is there to drag and tap", async () => {
    await render(sheet());
    expect(screen.getByTestId("sheet-grabber")).toBeTruthy();
    expect(screen.getByTestId("sheet-body")).toBeTruthy();
    expect(screen.getByTestId("sheet-grabber").props.accessibilityLabel).toBe("Hide details");
  });

  test("no full state when the content fits: the body does not scroll; a clipped body does", async () => {
    await render(sheet());
    const body = screen.getByTestId("sheet-body");
    await fireEvent(body, "layout", { nativeEvent: { layout: { x: 0, y: 0, width: 390, height: 200 } } });
    await fireEvent(body, "contentSizeChange", 390, 200);
    expect(screen.getByTestId("sheet-body").props.scrollEnabled).toBe(false);
    await fireEvent(body, "contentSizeChange", 390, 640);
    expect(screen.getByTestId("sheet-body").props.scrollEnabled).toBe(true);
  });
});

// UI review 2026-11-05 (ui-route-preview-light.png): Go is the one primary action before driving; the other
// routes and places are two 56 pt buttons; there is no "End route" before Go, only Cancel.
describe("CopilotSheet route preview", () => {
  const active = {
    place: { id: "p1", name: "Harbor Truck and Auto Plaza", category: "truck_stop", lat: 38.58, lng: -121.53, tags: ["DEF", "24/7", "7 truck spots", "9163722893"] },
    route: { summary: "11 min · 4.5 mi", distance_mi: 4.5, duration_min: 11, geometry: [], alternatives: [{ summary: "13 min · 5 mi" }, { summary: "14 min · 5 mi" }] },
    parking: null,
  } as unknown as import("../types").ActiveRoute;
  const preview = (driving: boolean, onEndRoute = noop, extra: { replyInSheet?: string | null; routesPanel?: React.ReactNode } = {}) => (
    <CopilotSheet
      {...extra}
      phase="navigating"
      speaking={false}
      onShowOptions={noop}
      options={[active.place, active.place, active.place, active.place]}
      parking={{}}
      onOptionParking={noop}
      onOptionRoute={noop}
      active={active}
      driving={driving}
      remaining={driving ? { m: 7242, s: 660 } : null}
      onGo={noop}
      bottomInset={0}
      onAvatarPress={noop}
      onSubmitText={noop}
      onClose={noop}
      onEndRoute={onEndRoute}
      onShowRoutes={noop}
      onLayout={noop}
    />
  );

  test("before Go: Cancel instead of End route, routes and places as 56 pt buttons, a readable tag line", async () => {
    const onEnd = jest.fn();
    await render(preview(false, onEnd));
    expect(screen.queryByTestId("end-route-button")).toBeNull();
    await fireEvent.press(screen.getByTestId("cancel-route-button"));
    expect(onEnd).toHaveBeenCalledTimes(1);
    for (const id of ["routes-chip", "other-options-button", "go-button"]) {
      // PressableScale: the testID is on the Pressable, the size on the view inside it.
      const inner = screen.getByTestId(id).children[0] as unknown as { props: { style: unknown } };
      expect(StyleSheet.flatten(inner.props.style as never).height).toBeGreaterThanOrEqual(56);
    }
    expect(screen.getByText("4 other places")).toBeTruthy();
    expect(screen.getByTestId("route-place-tags").props.children).toBe("DEF · 24/7 · 7 truck spots · (916) 372-2893");
  });

  test("a long answer and a permit line never push Routes and other places below the fold: they sit with Go outside the scroll (M-3)", async () => {
    const long = "Alright, got one. Harbor Truck and Auto Plaza, right off West Capitol Ave, about 2.9 miles out. Heads up: Your truck is over the legal height limit in CA (13 ft 6 in); that takes a permit.";
    const permit = { ...active, route: { ...active.route, restricted: "legal", notices: ["Your truck is over the legal height limit in CA (13 ft 6 in); that takes a permit."] } } as typeof active;
    await render(
      <CopilotSheet
        replyInSheet={long}
        phase="navigating"
        speaking={false}
        onShowOptions={noop}
        options={[active.place, active.place, active.place, active.place]}
        parking={{}}
        onOptionParking={noop}
        onOptionRoute={noop}
        active={permit}
        driving={false}
        remaining={null}
        onGo={noop}
        bottomInset={0}
        onAvatarPress={noop}
        onSubmitText={noop}
        onClose={noop}
        onEndRoute={noop}
        onShowRoutes={noop}
        onLayout={noop}
      />,
    );
    const body = screen.getByTestId("sheet-body");
    expect(within(body).getByTestId("sheet-reply")).toBeTruthy();
    expect(within(body).getByTestId("route-notices")).toBeTruthy();
    for (const id of ["routes-chip", "other-options-button", "go-button"]) {
      expect(screen.getByTestId(id)).toBeTruthy();
      expect(within(body).queryByTestId(id)).toBeNull();
    }
  });

  test("driving: End route is there, Cancel is gone", async () => {
    await render(preview(true));
    expect(screen.getByTestId("end-route-button")).toBeTruthy();
    expect(screen.queryByTestId("cancel-route-button")).toBeNull();
  });

  test("no room for the bubble: the Russian answer is at the top of the route card, word for word (N1)", async () => {
    const { useLangStore } = jest.requireActual<typeof import("../i18n")>("../i18n");
    useLangStore.setState({ lang: "ru" });
    try {
      const reply = "Нашёл: Harbor Truck and Auto Plaza, примерно 4 мили.";
      await render(preview(false, noop, { replyInSheet: reply }));
      expect(screen.getByTestId("sheet-reply").props.children).toBe(reply);
    } finally {
      useLangStore.setState({ lang: "en" });
    }
  });

  test("the routes list takes the route card's place in the sheet; Go waits until a route is picked (N7)", async () => {
    const { Text } = jest.requireActual<typeof import("react-native")>("react-native");
    await render(preview(false, noop, { routesPanel: <Text testID="routes-panel-stub">routes</Text> }));
    expect(screen.getByTestId("routes-panel-stub")).toBeTruthy();
    expect(screen.queryByTestId("route-place-name")).toBeNull();
    expect(screen.queryByTestId("go-button")).toBeNull();
  });
});

describe("CopilotSheet type row (UI review 2026-11-05, N8)", () => {
  test("the field, Send and Close are 56 pt", async () => {
    await render(sheet());
    await fireEvent.press(screen.getByTestId("type-button"));
    expect(StyleSheet.flatten(screen.getByTestId("copilot-text-input").props.style).minHeight).toBeGreaterThanOrEqual(56);
    await fireEvent.changeText(screen.getByTestId("copilot-text-input"), "fuel");
    for (const id of ["copilot-send-button", "copilot-text-close"]) {
      const inner = screen.getByTestId(id).children[0] as unknown as { props: { style: unknown } };
      const st = StyleSheet.flatten(inner.props.style as never) as { width: number; height: number };
      expect(st.width).toBeGreaterThanOrEqual(56);
      expect(st.height).toBeGreaterThanOrEqual(56);
    }
  });
});
