import { fireEvent, render, screen } from "@testing-library/react-native";
import { StyleSheet } from "react-native";

import { AlternativesSheet, routeFacts } from "../components/AlternativesSheet";
import type { RouteInfo } from "../types";

// Reanimated's native worklets are not installed under jest (its shipped mock still loads them):
// plain views, no-op animations.
jest.mock("react-native-reanimated", () => {
  const { View } = jest.requireActual<typeof import("react-native")>("react-native");
  const chain = { duration: () => chain, springify: () => chain, damping: () => chain, delay: () => chain };
  return {
    __esModule: true,
    default: { View },
    FadeIn: chain,
    FadeInDown: chain,
    FadeOutDown: chain,
    useSharedValue: (v: unknown) => {
      const sv = { value: v, get: () => sv.value, set: (n: unknown) => void (sv.value = n) };
      return sv;
    },
    useAnimatedStyle: (f: () => object) => f(),
    withSpring: (v: unknown) => v,
  };
});
// The icon font needs a native module; a text stand-in keeps the test about the routes.
jest.mock("../components/Icon", () => {
  const { Text } = jest.requireActual<typeof import("react-native")>("react-native");
  return { Icon: ({ name }: { name: string }) => <Text>{`icon:${name}`}</Text> };
});

const route = (summary: string, extra: Partial<RouteInfo> = {}): RouteInfo => ({
  geometry: [
    [38.58, -121.49],
    [38.55, -121.42],
  ],
  distance_m: 6759,
  duration_s: 492,
  distance_mi: 4.2,
  duration_min: 8,
  summary,
  source: "here",
  ...extra,
});

const primary = route("I-5 N", {
  labels: ["I-5"],
  alternatives: [
    route("I-80 E", { labels: ["I-80"], duration_s: 3900, distance_mi: 61.3, tolls_usd: 12.5, handle: "h-80" }),
    route("Side streets", { duration_s: 600, distance_mi: 5.04 }),
  ],
});

// RNTL 14: render() and fireEvent.* are async.
describe("AlternativesSheet", () => {
  test("lists the current route and each alternative with its facts", async () => {
    await render(<AlternativesSheet primary={primary} onPick={() => {}} onClose={() => {}} />);
    expect(screen.getByTestId("alternative-current")).toBeTruthy();
    expect(screen.getByText("8 min · 4.2 mi · current")).toBeTruthy();
    expect(screen.getByText("I-80")).toBeTruthy();
    expect(screen.getByText("1 h 5 min · 61 mi · $12.50 tolls")).toBeTruthy();
    expect(screen.getByText("Alternative 2")).toBeTruthy();
    expect(screen.getByText("10 min · 5.0 mi")).toBeTruthy();
  });

  test("in Russian every unit and the current mark are Russian, English stays as it was (QA 2026-11-05, M-4)", async () => {
    const { useLangStore } = jest.requireActual<typeof import("../i18n")>("../i18n");
    useLangStore.setState({ lang: "ru" });
    try {
      await render(<AlternativesSheet primary={primary} onPick={() => {}} onClose={() => {}} />);
      expect(screen.getByText("8 мин · 4.2 мили · текущий")).toBeTruthy();
      expect(screen.getByText("1 ч 5 мин · 61 миля · $12.50 за платные дороги")).toBeTruthy();
      expect(screen.getByText("10 мин · 5.0 мили")).toBeTruthy();
    } finally {
      useLangStore.setState({ lang: "en" });
    }
  });

  test("tapping an alternative reports its index; close reports close", async () => {
    const onPick = jest.fn();
    const onClose = jest.fn();
    await render(<AlternativesSheet primary={primary} onPick={onPick} onClose={onClose} />);
    await fireEvent.press(screen.getByTestId("alternative-1"));
    expect(onPick).toHaveBeenCalledWith(1);
    await fireEvent.press(screen.getByTestId("alternatives-close"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  test("56 pt close and rows of at least 56 pt, every route drawn (UI review 2026-11-05, N7)", async () => {
    await render(<AlternativesSheet primary={primary} onPick={() => {}} onClose={() => {}} />);
    // PressableScale draws its box on the inner view; the plain current row carries its own style.
    type Node = { props: { style?: unknown }; children: unknown[] };
    const box = (id: string) => {
      const el = screen.getByTestId(id) as unknown as Node;
      const styled = el.props.style ? el : (el.children[0] as Node);
      return StyleSheet.flatten(styled.props.style as never) as { width?: number; height?: number; minHeight?: number };
    };
    expect(box("alternatives-close").width).toBeGreaterThanOrEqual(56);
    expect(box("alternatives-close").height).toBeGreaterThanOrEqual(56);
    for (const id of ["alternative-current", "alternative-0", "alternative-1"]) {
      expect(box(id).minHeight).toBeGreaterThanOrEqual(56);
    }
  });
});

describe("routeFacts", () => {
  test("tolls only when charged", () => {
    expect(routeFacts(route("x", { tolls_usd: 0 }))).toBe("8 min · 4.2 mi");
    expect(routeFacts(route("x", { tolls_usd: null }))).toBe("8 min · 4.2 mi");
    expect(routeFacts(route("x", { tolls_usd: 3 }))).toBe("8 min · 4.2 mi · $3.00 tolls");
  });
});
