// The turn banner: a long instruction is not cut (QA 2026-11-11: "Съезд 522 в сторону San Francisco на I-80…").
import { render, screen } from "@testing-library/react-native";
import { StyleSheet } from "react-native";

import { LONG_INSTRUCTION, NavBanner } from "../components/NavBanner";
import { useLangStore } from "../i18n";
import type { Maneuver } from "../types";

// Reanimated's native worklets are not installed under jest: plain views, no-op animations.
jest.mock("react-native-reanimated", () => {
  const { View } = jest.requireActual<typeof import("react-native")>("react-native");
  const anim: object = new Proxy(() => anim, { get: () => () => anim });
  return {
    __esModule: true,
    default: { View },
    FadeInRight: anim,
    FadeInUp: anim,
    ZoomIn: anim,
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

const noop = () => {};
const turn = (instruction: string): Maneuver => ({ action: "exit", direction: "right", instruction, lat: 0, lng: 0, index: 3 });
const banner = (next: Maneuver | null, toNextM = 644) => <NavBanner topInset={0} next={next} toNextM={toNextM} voiceOn onToggleVoice={noop} onLayout={noop} />;
const instr = () => screen.getByTestId("nav-instruction");
const fontSize = () => StyleSheet.flatten(instr().props.style).fontSize;

afterEach(() => useLangStore.setState({ lang: "en" }));

describe("NavBanner", () => {
  test("a usual instruction, English or Russian: three lines of the normal size, as before", async () => {
    for (const text of ["Поверни налево на J St", "Turn left onto J St", "Take exit 7 for Stockton Blvd", "x".repeat(LONG_INSTRUCTION)]) {
      const { unmount } = await render(banner(turn(text)));
      expect(instr().props.numberOfLines).toBe(3);
      expect(fontSize()).toBe(16);
      await unmount();
    }
  });

  test("a long exit is cut to its number and first direction; the whole sentence stays in the label", async () => {
    const long = "Съезд 522 в сторону San Francisco на I-80 W / US-50 W";
    expect(long.length).toBeGreaterThan(LONG_INSTRUCTION);
    await render(banner(turn(long)));
    expect(instr().props.children).toBe("Съезд 522 в сторону San Francisco");
    expect(instr().props.accessibilityLabel).toBe(long);
    expect(instr().props.numberOfLines).toBe(3);
    expect(fontSize()).toBe(16);
  });

  test("QA 2026-11-11: exit 518 with three signs and a bracketed road, in Russian", async () => {
    useLangStore.setState({ lang: "ru" });
    await render(banner(turn("Съезд 518 в сторону US-50 W/San Francisco/I-80 на I-80-BR W (Capital City Fwy)"), 500));
    expect(instr().props.children).toBe("Съезд 518 в сторону US-50 W");
    expect(instr().props.maxFontSizeMultiplier).toBe(1.3);
  });

  test("a very long English exit: the number and the airport stay, the rest goes; four lines at 14, scale capped", async () => {
    await render(banner(turn("Take exit 52B toward Sacramento International Airport/Woodland onto I-5 N")));
    expect(instr().props.children).toBe("Exit 52B toward Sacramento International Airport");
    expect(instr().props.numberOfLines).toBe(4);
    expect(fontSize()).toBe(14);
    expect(instr().props.maxFontSizeMultiplier).toBe(1.3);
    expect(instr().props.accessibilityLabel).toBe("Take exit 52B toward Sacramento International Airport/Woodland onto I-5 N");
  });

  test("snapshot of the long-exit instruction as rendered", async () => {
    await render(banner(turn("Take exit 52B toward Sacramento International Airport/Woodland onto I-5 N"), 805));
    const { children, numberOfLines, maxFontSizeMultiplier, accessibilityLabel } = instr().props;
    expect({ distance: screen.getByTestId("nav-distance").props.children, children, numberOfLines, maxFontSizeMultiplier, accessibilityLabel, fontSize: fontSize() }).toMatchInlineSnapshot(`
{
  "accessibilityLabel": "Take exit 52B toward Sacramento International Airport/Woodland onto I-5 N",
  "children": "Exit 52B toward Sacramento International Airport",
  "distance": "0.5 mi",
  "fontSize": 14,
  "maxFontSizeMultiplier": 1.3,
  "numberOfLines": 4,
}
`);
  });

  test("no maneuver yet, in Russian; whole miles on the banner read as a number of miles", async () => {
    useLangStore.setState({ lang: "ru" });
    await render(banner(null, 2 * 1609.34));
    expect(instr().props.children).toBe("Следуй по маршруту");
    expect(instr().props.numberOfLines).toBe(3);
    expect(screen.getByTestId("nav-distance").props.children).toBe("2 мили"); // was "2,0 мили"
  });
});
