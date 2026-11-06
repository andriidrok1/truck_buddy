// UI review 2026-11-05, second pass: in the Russian UI the copilot's answer was on screen nowhere (N1). The
// Russian route card is taller, the map window above it fell under the bubble's bar, and the route card had no
// place for the text. These pin the bubble itself in Russian, the bar, and the 56 pt close (N3) and the note
// that the bubble's edge cut through (N4).
import { fireEvent, render, screen } from "@testing-library/react-native";
import { StyleSheet } from "react-native";

import { CaptionBubble } from "../components/Toast";
import { replyOnMap } from "../dialog/caption";
import { useLangStore } from "../i18n";

jest.mock("react-native-reanimated", () => {
  const { View } = jest.requireActual<typeof import("react-native")>("react-native");
  const chain = { duration: () => chain };
  return { __esModule: true, default: { View }, FadeInUp: chain, FadeOutUp: chain };
});
jest.mock("../components/Icon", () => {
  const { Text } = jest.requireActual<typeof import("react-native")>("react-native");
  return { Icon: ({ name }: { name: string }) => <Text>{`icon:${name}`}</Text> };
});

const RU_REPLY = "Нашёл: Harbor Truck and Auto Plaza, прямо у Harbor Blvd, примерно 4 мили.";

afterEach(() => useLangStore.setState({ lang: "en" }));

describe("CaptionBubble in Russian", () => {
  test("a Cyrillic answer is drawn whole and unchanged with lang=ru", async () => {
    useLangStore.setState({ lang: "ru" });
    await render(<CaptionBubble text={RU_REPLY} heard="где встать на ночь" bottom={300} maxHeight={120} onDismiss={() => {}} />);
    const text = screen.getByTestId("agent-reply-text");
    expect(text.props.children).toBe(RU_REPLY);
    expect(text.props.numberOfLines).toBeUndefined();
    const style = StyleSheet.flatten(text.props.style);
    expect(style.fontSize).toBeGreaterThanOrEqual(17);
    expect(style.opacity ?? 1).toBe(1);
    expect(style.display).not.toBe("none");
  });

  test("the Russian route preview leaves enough map for the bubble; only a tiny window sends it to the sheet", () => {
    // ui2-speaking-ru-light: about 180 layout units of map above the Russian route card.
    expect(replyOnMap(180)).toBe(true);
    expect(replyOnMap(270)).toBe(true);
    expect(replyOnMap(100)).toBe(false);
  });
});

describe("CaptionBubble layout", () => {
  test("the close button is a 56 pt target and closes", async () => {
    const onDismiss = jest.fn();
    await render(<CaptionBubble text="Alright, got one." bottom={300} onDismiss={onDismiss} />);
    const close = screen.getByTestId("agent-reply-dismiss");
    const style = StyleSheet.flatten(close.props.style);
    expect(style.width).toBeGreaterThanOrEqual(56);
    expect(style.height).toBeGreaterThanOrEqual(56);
    await fireEvent.press(close);
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });

  test("the note sits outside the scrolling answer, so the bubble's edge never cuts it", async () => {
    await render(<CaptionBubble text="Alright, got one." note="Parking looks good there." bottom={300} maxHeight={120} />);
    const inScroll = (id: string) => {
      let n = screen.getByTestId(id).parent;
      while (n && n.type !== "RCTScrollView") n = n.parent;
      return !!n;
    };
    expect(inScroll("agent-reply-text")).toBe(true);
    expect(inScroll("agent-reply-note")).toBe(false);
  });

  test("reports its height for the map padding, and 0 once it is gone", async () => {
    const onHeight = jest.fn();
    const { rerender } = await render(<CaptionBubble text="Alright, got one." bottom={300} onHeight={onHeight} />);
    await fireEvent(screen.getByTestId("agent-reply-caption"), "layout", { nativeEvent: { layout: { height: 132 } } });
    expect(onHeight).toHaveBeenLastCalledWith(132);
    await rerender(<CaptionBubble text={null} bottom={300} onHeight={onHeight} />);
    expect(onHeight).toHaveBeenLastCalledWith(0);
  });
});
