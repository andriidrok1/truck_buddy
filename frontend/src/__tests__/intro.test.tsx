// The three intro screens after the language choice (Figma f5Uha6gopQHRmcGlMXdCD9, B1-B3): paging, Skip, the
// once-only flag, the Russian side, and the rule that an install from before the intro never sees it.
import AsyncStorage from "@react-native-async-storage/async-storage";
import { act, fireEvent, render, renderHook, screen } from "@testing-library/react-native";
import { BackHandler, StyleSheet } from "react-native";

import { IntroGate } from "../components/intro/IntroGate";
import { CARD_BELOW_FONT_SCALE } from "../components/intro/IntroLot";
import { giveConsent, forgetConsent } from "../consent";
import { setOnboarded } from "../hooks/useTruckProfile";
import { t, useLangStore } from "../i18n";
import { RU } from "../i18n/ru";
import { forgetIntro, INTRO_KEY, loadIntro, markIntroSeen, resetIntroForTests, useIntroSeen } from "../state/intro";
import { storage } from "../utils/storage";

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
jest.mock("react-native-safe-area-context", () => ({ useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }) }));
jest.mock("../components/Icon", () => {
  const { Text } = jest.requireActual<typeof import("react-native")>("react-native");
  return { Icon: ({ name }: { name: string }) => <Text>{`icon:${name}`}</Text> };
});

const WIDTH = 750; // the window under jest
const swipeTo = (page: number) =>
  fireEvent.scroll(screen.getByTestId("intro-pager"), {
    nativeEvent: { contentOffset: { x: page * WIDTH, y: 0 }, contentSize: { width: 3 * WIDTH, height: 1000 }, layoutMeasurement: { width: WIDTH, height: 1000 } },
  });
// What a scroll started by a button looks like from the pager: offsets on the way from one screen to the next,
// the first ones still nearer the old screen, then the target itself, then the momentum end.
const scrollAt = (x: number, type = "scroll") =>
  fireEvent(screen.getByTestId("intro-pager"), type, {
    nativeEvent: { contentOffset: { x, y: 0 }, contentSize: { width: 3 * WIDTH, height: 1000 }, layoutMeasurement: { width: WIDTH, height: 1000 } },
  });
const animate = async (from: number, to: number, check?: () => void) => {
  for (const f of [0.05, 0.2, 0.45, 0.55, 0.8, 0.97]) {
    await scrollAt((from + (to - from) * f) * WIDTH);
    check?.();
  }
  await scrollAt(to * WIDTH);
  await scrollAt(to * WIDTH, "momentumScrollEnd");
};
/** Press the button and let the scroll it starts run to the end. */
const pressNext = async (from: number) => {
  await fireEvent.press(screen.getByTestId("intro-next"));
  await animate(from, from + 1);
};
const dotsLabel = () => screen.getByTestId("intro-dots").props.accessibilityLabel;
// RNTL leaves out what a screen reader does not get (the screens out of view, the decoration) unless asked.
const ALL = { includeHiddenElements: true };
const hidden = (n: number) => screen.queryByTestId(`intro-page-${n}`) === null && screen.getByTestId(`intro-page-${n}`, ALL) !== null;

beforeEach(async () => {
  resetIntroForTests();
  await AsyncStorage.clear();
  await forgetConsent();
  useLangStore.setState({ lang: "en", chosen: true, loaded: true });
});

// RNTL 14: render() and fireEvent.* are async.
describe("IntroGate", () => {
  test("the first screen: the promise, a Next button, no Skip yet", async () => {
    await render(<IntroGate />);
    expect(screen.getByText("Talk, don’t tap")).toBeTruthy();
    expect(screen.getByText("A copilot for truck drivers. Ask out loud, keep your hands on the wheel.")).toBeTruthy();
    expect(screen.getByText("Next")).toBeTruthy();
    expect(screen.queryByTestId("intro-skip")).toBeNull();
    expect(dotsLabel()).toBe("Page 1 of 3");
    expect(hidden(1)).toBe(false);
    expect(hidden(2)).toBe(true);
    expect(hidden(3)).toBe(true);
  });

  test("all three screens carry their content", async () => {
    await render(<IntroGate />);
    for (const line of [
      "You say",
      "“Where can I park tonight?”",
      "I answer",
      "Roady’s Truck Stop",
      "4 miles",
      "parking looks good",
      "Just ask",
      "No typing, no menus. Hands stay on the wheel.",
      "I check the lot first",
      "Truck parking",
      "Way in",
      "Sacramento 49er Travel Plaza",
      "Likely fits your truck",
    ]) {
      expect(screen.getByText(line, ALL)).toBeTruthy();
    }
    expect(screen.getByLabelText("Aerial picture of a truck stop with the truck parking and the way in marked", ALL)).toBeTruthy();
  });

  test("a screen reader gets the screen in view without the decoration: the wave, the marks on the picture", async () => {
    await render(<IntroGate />);
    await swipeTo(1);
    expect(screen.getByText("“Where can I park tonight?”")).toBeTruthy();
    expect(screen.getByText("Roady’s Truck Stop")).toBeTruthy();
    expect(screen.queryByTestId("intro-ask-wave")).toBeNull();
    expect(screen.getByTestId("intro-ask-wave", ALL)).toBeTruthy();
    expect(screen.queryByText("Talk, don’t tap")).toBeNull(); // the screen before, out of view
    await swipeTo(2);
    expect(screen.getByText("I check the lot first")).toBeTruthy();
    expect(screen.getByLabelText("Aerial picture of a truck stop with the truck parking and the way in marked")).toBeTruthy();
    expect(screen.getByText("Sacramento 49er Travel Plaza")).toBeTruthy();
    expect(screen.getByText("Likely fits your truck")).toBeTruthy();
    expect(screen.queryByText("Truck parking")).toBeNull();
    expect(screen.queryByText("Way in")).toBeNull();
    expect(screen.getByTestId("intro-lot-marks", ALL)).toBeTruthy();
  });

  test("Next, Next, Continue: the key is written once and onDone is called once", async () => {
    const onDone = jest.fn();
    const write = jest.spyOn(storage, "setItem");
    await render(<IntroGate onDone={onDone} />);

    await pressNext(0);
    expect(dotsLabel()).toBe("Page 2 of 3");
    expect(screen.getByTestId("intro-skip")).toBeTruthy();
    expect(screen.getByText("Next")).toBeTruthy();
    expect(hidden(1)).toBe(true);
    expect(hidden(2)).toBe(false);

    await pressNext(1);
    expect(dotsLabel()).toBe("Page 3 of 3");
    expect(screen.getByText("Continue")).toBeTruthy();
    expect(screen.queryByText("Next")).toBeNull();
    expect(onDone).not.toHaveBeenCalled();
    expect(await storage.getItem(INTRO_KEY, null)).toBeNull();

    await fireEvent.press(screen.getByTestId("intro-next"));
    await fireEvent.press(screen.getByTestId("intro-next")); // a second tap before the root has switched
    expect(onDone).toHaveBeenCalledTimes(1);
    expect(write.mock.calls.filter(([k]) => k === INTRO_KEY)).toEqual([[INTRO_KEY, "seen"]]);
    expect(await storage.getItem(INTRO_KEY, null)).toBe("seen");
    expect(await loadIntro()).toBe(true);
    write.mockRestore();
  });

  test("Skip on the second screen goes straight past the intro", async () => {
    const onDone = jest.fn();
    const write = jest.spyOn(storage, "setItem");
    await render(<IntroGate onDone={onDone} />);
    await pressNext(0);
    await fireEvent.press(screen.getByTestId("intro-skip"));
    expect(onDone).toHaveBeenCalledTimes(1);
    expect(write.mock.calls.filter(([k]) => k === INTRO_KEY)).toEqual([[INTRO_KEY, "seen"]]);
    expect(await loadIntro()).toBe(true);
    write.mockRestore();
  });

  test("the dots, Skip and the button follow a swipe", async () => {
    await render(<IntroGate />);
    await swipeTo(1);
    expect(dotsLabel()).toBe("Page 2 of 3");
    expect(screen.getAllByTestId("intro-dot-on")).toHaveLength(1);
    expect(screen.getAllByTestId("intro-dot")).toHaveLength(2);
    expect(screen.getByTestId("intro-skip")).toBeTruthy();
    await swipeTo(2);
    expect(dotsLabel()).toBe("Page 3 of 3");
    expect(screen.getByText("Continue")).toBeTruthy();
    expect(hidden(3)).toBe(false);
    await swipeTo(0);
    expect(dotsLabel()).toBe("Page 1 of 3");
    expect(screen.queryByTestId("intro-skip")).toBeNull();
    expect(screen.getByText("Next")).toBeTruthy();
    expect(await storage.getItem(INTRO_KEY, null)).toBeNull(); // swiping around is not finishing
  });

  test("in Russian every line and label is Russian", async () => {
    useLangStore.setState({ lang: "ru" });
    await render(<IntroGate />);
    for (const line of [
      "Говори, не нажимай",
      "Штурман для дальнобойщиков. Спрашивай вслух, руки держи на руле.",
      "Ты говоришь",
      "«Где встать на ночь?»",
      "Я отвечаю",
      "с парковкой порядок",
      "Просто спроси",
      "Без набора и меню. Руки остаются на руле.",
      "Сначала проверяю стоянку",
      "Парковка для траков",
      "Въезд",
      "Трак, похоже, встанет",
      "4 мили",
      "Дальше",
    ]) {
      expect(screen.getByText(line, ALL)).toBeTruthy();
    }
    expect(dotsLabel()).toBe("Страница 1 из 3");
    expect(screen.getByTestId("intro-next").props.accessibilityLabel).toBe("Дальше");
    await swipeTo(2);
    expect(screen.getByLabelText("Снимок трак-стопа сверху, отмечены парковка для траков и въезд")).toBeTruthy();
    expect(screen.getByText("Продолжить")).toBeTruthy();
    expect(screen.getByTestId("intro-skip").props.accessibilityLabel).toBe("Пропустить");
    expect(screen.getByText("Пропустить")).toBeTruthy();
  });

  test("a button's scroll in flight: the dots, Skip and the label go to the next screen at once and do not flicker back", async () => {
    await render(<IntroGate />);
    await fireEvent.press(screen.getByTestId("intro-next"));
    const onSecond = () => {
      expect(dotsLabel()).toBe("Page 2 of 3");
      expect(screen.getByTestId("intro-skip")).toBeTruthy();
      expect(hidden(1)).toBe(true);
      expect(hidden(2)).toBe(false);
    };
    onSecond();
    await animate(0, 1, onSecond); // the first offsets still round to screen 1
    onSecond();
    // Landed: the pager listens to the driver's finger again.
    await swipeTo(0);
    expect(dotsLabel()).toBe("Page 1 of 3");
  });

  test("a fast double tap on the second screen shows the third, it does not finish the intro behind it", async () => {
    const onDone = jest.fn();
    await render(<IntroGate onDone={onDone} />);
    await pressNext(0);
    await fireEvent.press(screen.getByTestId("intro-next"));
    await scrollAt(1.1 * WIDTH); // the scroll to screen 3 has only started
    await fireEvent.press(screen.getByTestId("intro-next"));
    await fireEvent.press(screen.getByTestId("intro-next"));
    expect(onDone).not.toHaveBeenCalled();
    expect(dotsLabel()).toBe("Page 3 of 3");
    expect(await storage.getItem(INTRO_KEY, null)).toBeNull();
    await animate(1.1, 2);
    await fireEvent.press(screen.getByTestId("intro-next"));
    expect(onDone).toHaveBeenCalledTimes(1);
  });

  test("a double tap on the first screen moves one screen, not two", async () => {
    await render(<IntroGate />);
    await fireEvent.press(screen.getByTestId("intro-next"));
    await fireEvent.press(screen.getByTestId("intro-next"));
    await animate(0, 1);
    expect(dotsLabel()).toBe("Page 2 of 3");
  });

  test("a scroll that never reports its end (animations off) frees the button after a moment", async () => {
    const onDone = jest.fn();
    await render(<IntroGate onDone={onDone} />);
    await fireEvent.press(screen.getByTestId("intro-next"));
    await act(() => new Promise((r) => setTimeout(r, 700)));
    await fireEvent.press(screen.getByTestId("intro-next"));
    expect(dotsLabel()).toBe("Page 3 of 3");
    expect(onDone).not.toHaveBeenCalled();
  });

  test("the driver's finger takes over from a button's scroll", async () => {
    await render(<IntroGate />);
    await fireEvent.press(screen.getByTestId("intro-next"));
    await scrollAt(0.3 * WIDTH);
    await scrollAt(0.3 * WIDTH, "scrollBeginDrag");
    await scrollAt(0.1 * WIDTH);
    await scrollAt(0, "momentumScrollEnd");
    expect(dotsLabel()).toBe("Page 1 of 3");
    expect(screen.queryByTestId("intro-skip")).toBeNull();
  });

  describe("Android Back", () => {
    type Handler = Parameters<typeof BackHandler.addEventListener>[1];
    let handlers: Handler[];
    let removed: number;
    let spy: jest.SpyInstance;
    beforeEach(() => {
      handlers = [];
      removed = 0;
      spy = jest.spyOn(BackHandler, "addEventListener").mockImplementation((_event, handler) => {
        handlers.push(handler);
        return {
          remove: () => {
            removed++;
            handlers = handlers.filter((h) => h !== handler);
          },
        };
      });
    });
    afterEach(() => spy.mockRestore());
    /** The system asks the newest listener first; true = handled, the app stays. */
    const pressBack = async () => {
      const answers: unknown[] = [];
      await act(async () => {
        answers.push((handlers[handlers.length - 1] as unknown as () => unknown)());
      });
      return answers[0] === true;
    };

    test("on the first screen the system has it", async () => {
      await render(<IntroGate />);
      expect(await pressBack()).toBe(false);
      expect(dotsLabel()).toBe("Page 1 of 3");
    });

    test("from the third screen back to the second, then the first, then out to the system; nothing is marked seen", async () => {
      const onDone = jest.fn();
      await render(<IntroGate onDone={onDone} />);
      await swipeTo(2);
      expect(await pressBack()).toBe(true);
      expect(dotsLabel()).toBe("Page 2 of 3");
      // Still on the way back: a second press is swallowed, the app does not close and no screen is skipped.
      expect(await pressBack()).toBe(true);
      expect(dotsLabel()).toBe("Page 2 of 3");
      await animate(2, 1);
      expect(await pressBack()).toBe(true);
      await animate(1, 0);
      expect(dotsLabel()).toBe("Page 1 of 3");
      expect(screen.queryByTestId("intro-skip")).toBeNull();
      expect(await pressBack()).toBe(false);
      expect(onDone).not.toHaveBeenCalled();
      expect(await storage.getItem(INTRO_KEY, null)).toBeNull();
    });

    test("one listener at a time, none left after the intro is gone", async () => {
      const view = await render(<IntroGate />);
      await swipeTo(1);
      await swipeTo(2);
      expect(handlers).toHaveLength(1);
      await view.unmount();
      expect(handlers).toHaveLength(0);
      expect(removed).toBe(spy.mock.calls.length);
    });
  });

  describe("the picture screen", () => {
    const flat = (id: string) => StyleSheet.flatten(screen.getByTestId(id, ALL).props.style);
    const dims = jest.requireActual<typeof import("react-native")>("react-native");
    const withWindow = (w: { width: number; height: number; fontScale: number }) =>
      jest.spyOn(dims, "useWindowDimensions").mockReturnValue({ ...w, scale: 2 });
    afterEach(() => jest.restoreAllMocks());

    test("the picture is as wide as the button and keeps its 4:5 shape; the card lies on it", async () => {
      withWindow({ width: 400, height: 800, fontScale: 1 });
      await render(<IntroGate />);
      const box = flat("intro-lot-picture");
      expect(box.width).toBe(400 - 2 * 24); // the button's side margins
      expect(box.height).toBe(Math.floor(((400 - 48) * 5) / 4));
      expect(flat("intro-lot-card").position).toBe("absolute");
    });

    test("a short room under the heading makes the picture smaller, not cut", async () => {
      withWindow({ width: 400, height: 600, fontScale: 1 });
      await render(<IntroGate />);
      await fireEvent(screen.getByTestId("intro-lot-picture", ALL).parent!, "layout", { nativeEvent: { layout: { x: 0, y: 0, width: 352, height: 300 } } });
      const box = flat("intro-lot-picture");
      expect(box.height).toBe(300);
      expect(box.width).toBe(240);
    });

    test("the tags hold on to the area and the arrow by the edge that faces them, whatever the font size", async () => {
      withWindow({ width: 400, height: 800, fontScale: 1.3 });
      await render(<IntroGate />);
      const parking = flat("intro-lot-tag-parking");
      expect(parking.top).toBeUndefined();
      expect(parking.bottom).toBeGreaterThan(0); // grows upwards, away from the outline
      const wayIn = flat("intro-lot-tag-way-in");
      expect(wayIn.bottom).toBeUndefined();
      expect(wayIn.top).toBeGreaterThan(0);
    });

    test("the card is two lines, cut rather than wrapped; with a very large font it sits under the picture", async () => {
      withWindow({ width: 400, height: 800, fontScale: CARD_BELOW_FONT_SCALE });
      const view = await render(<IntroGate />);
      expect(screen.getByText("Sacramento 49er Travel Plaza", ALL).props.numberOfLines).toBe(1);
      expect(screen.getByText("Likely fits your truck", ALL).props.numberOfLines).toBe(1);
      expect(flat("intro-lot-card").position).toBe("absolute");
      await view.unmount();

      jest.restoreAllMocks();
      withWindow({ width: 400, height: 800, fontScale: 1.5 });
      await render(<IntroGate />);
      const card = flat("intro-lot-card");
      expect(card.position).toBeUndefined();
      expect(card.width).toBe(flat("intro-lot-picture").width);
    });
  });

  test("the Russian side: informal, and no English left in it", () => {
    for (const en of ["Talk, don’t tap", "Just ask", "I check the lot first", "You say", "I answer", "Way in", "Likely fits your truck", "parking looks good"]) {
      expect(RU[en]).toBeTruthy();
      expect(t(en, undefined, "ru")).not.toMatch(/[A-Za-z]/);
      expect(t(en, undefined, "ru")).not.toMatch(/\bВы\b|Вам|Ваш|нажмите|спросите/);
    }
  });
});

describe("the intro flag (src/state/intro.ts)", () => {
  test("a fresh install has not seen the intro", async () => {
    expect(await loadIntro()).toBe(false);
    expect(await storage.getItem(INTRO_KEY, null)).toBeNull();
  });

  test("seen once, seen after a restart", async () => {
    await markIntroSeen();
    resetIntroForTests(); // a cold start
    expect(await loadIntro()).toBe(true);
  });

  test("an install that agreed to the consent screen before this version is past the intro, and stays so", async () => {
    await giveConsent();
    expect(await loadIntro()).toBe(true);
    await act(async () => {});
    expect(await storage.getItem(INTRO_KEY, null)).toBe("seen");
    // A new consent version puts the consent screen back; the intro does not come with it.
    await forgetConsent();
    resetIntroForTests();
    expect(await loadIntro()).toBe(true);
  });

  test("an install that finished onboarding before this version is past the intro (consent asked again)", async () => {
    await setOnboarded();
    expect(await loadIntro()).toBe(true);
  });

  test("Delete my data: the flag is gone and the intro is due again", async () => {
    await markIntroSeen();
    await forgetIntro();
    expect(await storage.getItem(INTRO_KEY, null)).toBeNull();
    expect(await loadIntro()).toBe(false);
    resetIntroForTests();
    expect(await loadIntro()).toBe(false);
  });

  test("the hook: undefined while the key is read, then the answer, and it follows the button", async () => {
    const hook = await renderHook(() => useIntroSeen());
    await act(async () => {});
    expect(hook.result.current).toBe(false);
    await act(async () => {
      await markIntroSeen();
    });
    expect(hook.result.current).toBe(true);
  });
});
