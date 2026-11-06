// UI review 2026-11-05, third pass: the English answer cut through the letters inside the bubble (H1), the answer
// hidden under the height card (M2), Follow truck on the history button (M3), the bare 48 pt Hazmat and Speed
// alerts switches (M4) and the route choice with only one line on the map (M1, the camera fit).
import { act, fireEvent, render, screen } from "@testing-library/react-native";
import { StyleSheet } from "react-native";

import { CaptionBubble } from "../components/Toast";
import { ToggleRow } from "../components/PrefsForm";
import { bubbleRoom, fitBubble, MIN_BUBBLE_ROOM, REPLY_LINE, replyPlacement } from "../dialog/caption";
import { routeFitPoints } from "../map/bounds";
import { floatingBottom } from "../state/floating";

jest.mock("react-native-reanimated", () => {
  const { View } = jest.requireActual<typeof import("react-native")>("react-native");
  const chain = { duration: () => chain };
  return {
    __esModule: true,
    default: { View },
    FadeInUp: chain,
    FadeOutUp: chain,
    useSharedValue: (v: unknown) => {
      const sv = { value: v, get: () => sv.value, set: (n: unknown) => void (sv.value = n) };
      return sv;
    },
    useAnimatedStyle: (f: () => object) => f(),
    withSpring: (v: unknown) => v,
  };
});
jest.mock("../components/Icon", () => {
  const { Text } = jest.requireActual<typeof import("react-native")>("react-native");
  return { Icon: ({ name }: { name: string }) => <Text>{`icon:${name}`}</Text> };
});

// ui3-speaking-note-light: four lines of answer under "You said", the note under them, a 118 pt bubble.
const EN_REPLY = "Alright, got one. Harbor Truck and Auto Plaza, right off Harbor Blvd, about 4.5 miles out.";
const PAD = 12;
const HEARD = 22; // "You said" line (18) + its 4 pt margin
const TEXT_Y = PAD + HEARD;
const FOUR_LINES = TEXT_Y + 4 * REPLY_LINE; // scroll content, no bottom padding
const NOTE_BELOW = 41; // 12 pt gap, the note's line (21) and its padding, -4 pt pull-up
const BARE_BELOW = 12; // the bottom padding alone

/** Lays the measured sizes onto the rendered bubble, as Android would. */
async function layOut(below: number) {
  await fireEvent(screen.getByTestId("agent-reply-text"), "layout", { nativeEvent: { layout: { y: TEXT_Y, height: 4 * REPLY_LINE } } });
  await fireEvent(screen.getByTestId("agent-reply-text"), "textLayout", { nativeEvent: { lines: [{ height: REPLY_LINE }] } });
  await fireEvent(screen.getByTestId("agent-reply-scroll"), "contentSizeChange", 300, FOUR_LINES);
  await fireEvent(screen.getByTestId("agent-reply-below"), "layout", { nativeEvent: { layout: { height: below } } });
}

describe("H1: the answer is never cut through the letters", () => {
  test("a 4-line English answer with the note in the reviewed 118 pt bubble does not fit, and the screen is told", async () => {
    const onFit = jest.fn();
    await render(<CaptionBubble text={EN_REPLY} heard="where can I park tonight" note="Parking looks good there." bottom={300} maxHeight={118} onFit={onFit} onDismiss={() => {}} />);
    await layOut(NOTE_BELOW);
    expect(onFit).toHaveBeenLastCalledWith(false);
    // Where it stays in the bubble (no place in the sheet), the scroll area ends on a line boundary.
    const h = StyleSheet.flatten(screen.getByTestId("agent-reply-scroll").props.style).height as number;
    expect((h - TEXT_Y) % REPLY_LINE).toBe(0);
    expect(h - TEXT_Y).toBeGreaterThanOrEqual(REPLY_LINE);
    expect(h + NOTE_BELOW).toBeLessThanOrEqual(118);
  });

  test("with the room the third pass gives it (whole map window above the sheet), the same answer fits whole", async () => {
    const onFit = jest.fn();
    // ui3-speaking-light: map window about 214 pt, the bubble's bottom 32 pt above the sheet.
    const room = bubbleRoom(214, 32);
    await render(<CaptionBubble text={EN_REPLY} heard="where can I park tonight" bottom={300} maxHeight={room} onFit={onFit} />);
    await layOut(BARE_BELOW);
    expect(onFit).toHaveBeenLastCalledWith(true);
    expect(StyleSheet.flatten(screen.getByTestId("agent-reply-scroll").props.style).height).toBeUndefined();
  });

  test("fitBubble: every result is either the whole answer or a whole number of lines", () => {
    for (let room = 60; room <= 260; room += 1) {
      for (const below of [BARE_BELOW, NOTE_BELOW]) {
        const r = fitBubble({ room, textY: TEXT_Y, contentH: FOUR_LINES, belowH: below });
        if (r.fits) expect(FOUR_LINES + below).toBeLessThanOrEqual(room + 0.5);
        else expect(((r.scrollH as number) - TEXT_Y) % REPLY_LINE).toBe(0);
      }
    }
  });

  test("an answer that did not fit goes to the route card in the preview, and stays in the bubble where the sheet has no place", () => {
    expect(replyPlacement({ mapWindow: 214, room: 160, overflowed: true, sheetHosts: true })).toBe("sheet");
    expect(replyPlacement({ mapWindow: 214, room: 160, overflowed: false, sheetHosts: true })).toBe("map");
    expect(replyPlacement({ mapWindow: 214, room: 160, overflowed: true, sheetHosts: false })).toBe("map");
  });
});

describe("M2: the height card never hides the answer", () => {
  test("above a 146 pt card the room is too small for the bubble, so the answer goes to the sheet", () => {
    // ui3-reno-preview-heightcard-light: the card is 146 pt tall; the bubble's bottom sits on top of it.
    const lift = 32 + 146 + 8;
    const room = bubbleRoom(260, lift);
    expect(room).toBeLessThan(MIN_BUBBLE_ROOM);
    expect(replyPlacement({ mapWindow: 260, room, overflowed: false, sheetHosts: true })).toBe("sheet");
  });
});

describe("M3: Follow truck never sits on the history button", () => {
  test("the reviewed state (history down to 181 pt, Follow top at 164 pt) hides Follow", () => {
    const windowH = 914;
    // Follow 164–220 pt from the top: bottom = 914 - 220.
    expect(floatingBottom({ windowH, topH: 181, bottom: windowH - 220 })).toBeNull();
  });

  test("with room under the top bar it stays where it asked to be", () => {
    expect(floatingBottom({ windowH: 914, topH: 181, bottom: 400 })).toBe(400);
    // Exactly the 8 pt gap.
    expect(floatingBottom({ windowH: 914, topH: 181, bottom: 914 - 181 - 8 - 56 })).toBe(914 - 181 - 8 - 56);
    expect(floatingBottom({ windowH: 914, topH: 181, bottom: 914 - 181 - 7 - 56 })).toBeNull();
  });
});

describe("M1: the camera takes in every route while the driver picks one", () => {
  test("the fit corners cover the current route and the alternatives", () => {
    const current: [number, number][] = [[38.58, -121.49], [39.53, -119.81]];
    const viaCa89: [number, number][] = [[38.58, -121.49], [39.1, -120.1], [39.53, -119.81]];
    const viaI580: [number, number][] = [[38.58, -121.49], [38.9, -119.7], [39.53, -119.81]];
    const [sw, ne] = routeFitPoints([current, viaCa89, viaI580]);
    expect(sw.lat).toBeLessThan(38.58);
    expect(sw.lng).toBeLessThan(-121.49);
    expect(ne.lat).toBeGreaterThan(39.53);
    expect(ne.lng).toBeGreaterThan(-119.7);
    expect(routeFitPoints([])).toEqual([]);
  });
});

describe("M4: Hazmat and Speed limit alerts toggle from the whole row", () => {
  test("a tap on the row flips the switch and the row is a 56 pt target", async () => {
    const onChange = jest.fn();
    await render(<ToggleRow title="Hazardous materials" subtitle="Avoid tunnels and roads closed to hazmat" value={false} onChange={onChange} testID="truck-hazmat-switch" />);
    const row = screen.getByTestId("truck-hazmat-switch-row");
    // PressableScale: the Pressable is the target, its child carries the row's size.
    const body = row.children[0] as unknown as { props: { style: unknown } };
    const style = StyleSheet.flatten(body.props.style as never) as { minHeight?: number };
    expect(style.minHeight).toBeGreaterThanOrEqual(56);
    await act(async () => {
      fireEvent.press(row);
    });
    expect(onChange).toHaveBeenCalledWith(true);
    expect(screen.getByTestId("truck-hazmat-switch")).toBeTruthy();
    expect(screen.getByText("Avoid tunnels and roads closed to hazmat")).toBeTruthy();
  });
});

describe("H1: a new answer is measured afresh", () => {
  test("a short answer after a long one is not judged by the long one's size", async () => {
    const onFit = jest.fn();
    const { rerender } = await render(<CaptionBubble text={EN_REPLY} bottom={300} maxHeight={118} onFit={onFit} />);
    await layOut(NOTE_BELOW);
    expect(onFit).toHaveBeenLastCalledWith(false);
    onFit.mockClear();
    await rerender(<CaptionBubble text="Done." bottom={300} maxHeight={118} onFit={onFit} />);
    expect(onFit).not.toHaveBeenCalled();
    await fireEvent(screen.getByTestId("agent-reply-text"), "layout", { nativeEvent: { layout: { y: PAD, height: REPLY_LINE } } });
    await fireEvent(screen.getByTestId("agent-reply-scroll"), "contentSizeChange", 300, PAD + REPLY_LINE);
    expect(onFit).toHaveBeenLastCalledWith(true);
  });
});
