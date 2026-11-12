// The three intro screens after the language choice (Figma f5Uha6gopQHRmcGlMXdCD9, B1-B3): paging, Skip, the
// once-only flag, the Russian side, and the rule that an install from before the intro never sees it.
import AsyncStorage from "@react-native-async-storage/async-storage";
import { act, fireEvent, render, renderHook, screen } from "@testing-library/react-native";

import { IntroGate } from "../components/intro/IntroGate";
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
    expect(screen.getByText(/^4 miles · /, ALL)).toBeTruthy();
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

    await fireEvent.press(screen.getByTestId("intro-next"));
    expect(dotsLabel()).toBe("Page 2 of 3");
    expect(screen.getByTestId("intro-skip")).toBeTruthy();
    expect(screen.getByText("Next")).toBeTruthy();
    expect(hidden(1)).toBe(true);
    expect(hidden(2)).toBe(false);

    await fireEvent.press(screen.getByTestId("intro-next"));
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
    await fireEvent.press(screen.getByTestId("intro-next"));
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
      "Твой трак, скорее всего, встанет",
      "Дальше",
    ]) {
      expect(screen.getByText(line, ALL)).toBeTruthy();
    }
    expect(screen.getByText(/^4 мили · /, ALL)).toBeTruthy();
    expect(dotsLabel()).toBe("Страница 1 из 3");
    expect(screen.getByTestId("intro-next").props.accessibilityLabel).toBe("Дальше");
    await swipeTo(2);
    expect(screen.getByLabelText("Снимок трак-стопа сверху, отмечены парковка для траков и въезд")).toBeTruthy();
    expect(screen.getByText("Продолжить")).toBeTruthy();
    expect(screen.getByTestId("intro-skip").props.accessibilityLabel).toBe("Пропустить");
    expect(screen.getByText("Пропустить")).toBeTruthy();
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
