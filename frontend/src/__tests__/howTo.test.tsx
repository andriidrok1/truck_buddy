// "How to use" (Figma f5Uha6gopQHRmcGlMXdCD9, section 68:2): the screen, its chips and buttons, the once-only
// auto-show after onboarding, the dot on "?" and the Russian side.
import { act, fireEvent, render, renderHook, screen } from "@testing-library/react-native";

import { HOW_TO_CHIPS, HowToSheet } from "../components/HowToSheet";
import { TopBar } from "../components/TopBar";
import { useHowToFlow } from "../hooks/useHowToFlow";
import { RU } from "../i18n/ru";
import { t, useLangStore } from "../i18n";
import { HOW_TO_KEY, howToDot, loadHowTo, markHowToDue, markHowToSeen, resetHowToForTests, shouldAutoShowHowTo } from "../state/howTo";
import { storage } from "../utils/storage";

jest.mock("react-native-reanimated", () => {
  const { View } = jest.requireActual<typeof import("react-native")>("react-native");
  return {
    __esModule: true,
    default: { View },
    FadeInDown: { springify: () => ({ damping: () => ({}) }) },
    useSharedValue: (v: unknown) => {
      const sv = { value: v, get: () => sv.value, set: (n: unknown) => void (sv.value = n) };
      return sv;
    },
    useAnimatedStyle: (f: () => object) => f(),
    withSpring: (v: unknown) => v,
  };
});
jest.mock("react-native-safe-area-context", () => {
  const { View } = jest.requireActual<typeof import("react-native")>("react-native");
  return { useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }), SafeAreaView: View };
});
jest.mock("../components/Icon", () => {
  const { Text } = jest.requireActual<typeof import("react-native")>("react-native");
  return { Icon: ({ name }: { name: string }) => <Text>{`icon:${name}`}</Text> };
});
jest.mock("../components/Avatar", () => {
  const { View } = jest.requireActual<typeof import("react-native")>("react-native");
  return { Avatar: () => <View testID="avatar" /> };
});

const noop = () => {};
const flush = () => act(async () => {});

beforeEach(async () => {
  resetHowToForTests();
  await storage.removeItem(HOW_TO_KEY);
});
afterEach(() => useLangStore.setState({ lang: "en" }));

// RNTL 14: render() and fireEvent.* are async.
describe("HowToSheet", () => {
  test("the full screen: mascot, title, three steps, all six chips, the Motive badge and Try it now", async () => {
    await render(<HowToSheet mode="help" motive={false} onAsk={noop} onTryNow={noop} onClose={noop} />);
    expect(screen.getByTestId("avatar")).toBeTruthy();
    expect(screen.getByText("Just tap and ask")).toBeTruthy();
    expect(screen.getByText("No typing. Talk to me like you'd talk to a friend.")).toBeTruthy();
    for (const s of ["Tap me", "Say it your way", "I answer out loud", "Try saying", "Tap one and I ask it", "Try it now"]) {
      expect(screen.getByText(s)).toBeTruthy();
    }
    for (const c of HOW_TO_CHIPS) expect(screen.getByText(c.text)).toBeTruthy();
    expect(HOW_TO_CHIPS.map((c) => c.text)).toEqual([
      "Find me diesel",
      "Find a CAT scale",
      "Take me to Reno",
      "Weather in Reno",
      "Where can I park near Stockton?",
      "How many hours do I have?",
    ]);
    expect(screen.getByText("Needs Motive")).toBeTruthy();
    expect(screen.getByTestId("how-to-close")).toBeTruthy();
    expect(screen.queryByTestId("how-to-not-now")).toBeNull();
    expect(screen.queryByTestId("how-to-all-set")).toBeNull();
  });

  test("with Motive connected the badge is gone", async () => {
    await render(<HowToSheet mode="help" motive onAsk={noop} onTryNow={noop} onClose={noop} />);
    expect(screen.getByText("How many hours do I have?")).toBeTruthy();
    expect(screen.queryByTestId("how-to-motive-badge")).toBeNull();
  });

  test("first launch: the sheet greets by name (or without one), has Not now, and keeps the four short chips", async () => {
    const onClose = jest.fn();
    await render(<HowToSheet mode="first" name=" Mike " motive={false} onAsk={noop} onTryNow={noop} onClose={onClose} />);
    expect(screen.getByText("All set, Mike. One thing before we drive.")).toBeTruthy();
    expect(screen.getByTestId("how-to-chip-3")).toBeTruthy();
    expect(screen.queryByTestId("how-to-chip-4")).toBeNull();
    await fireEvent.press(screen.getByTestId("how-to-not-now"));
    expect(onClose).toHaveBeenCalledTimes(1);

    await render(<HowToSheet mode="first" name={null} motive={false} onAsk={noop} onTryNow={noop} onClose={noop} />);
    expect(screen.getByText("All set. One thing before we drive.")).toBeTruthy();
  });

  test("hidden without a mode", async () => {
    await render(<HowToSheet mode={null} motive={false} onAsk={noop} onTryNow={noop} onClose={noop} />);
    expect(screen.queryByTestId("how-to")).toBeNull();
  });

  test("a chip hands over its phrase, Try it now and × their own callbacks", async () => {
    const onAsk = jest.fn();
    const onTryNow = jest.fn();
    const onClose = jest.fn();
    await render(<HowToSheet mode="help" motive={false} onAsk={onAsk} onTryNow={onTryNow} onClose={onClose} />);
    await fireEvent.press(screen.getByTestId("how-to-chip-1"));
    expect(onAsk).toHaveBeenCalledWith("Find a CAT scale");
    await fireEvent.press(screen.getByTestId("how-to-try"));
    expect(onTryNow).toHaveBeenCalledTimes(1);
    await fireEvent.press(screen.getByTestId("how-to-close"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  test("in Russian the screen reads Russian and a chip sends the Russian phrase", async () => {
    useLangStore.setState({ lang: "ru" });
    const onAsk = jest.fn();
    await render(<HowToSheet mode="first" name="Миша" motive={false} onAsk={onAsk} onTryNow={noop} onClose={noop} />);
    expect(screen.getByText("Всё готово, Миша. Ещё одно перед дорогой.")).toBeTruthy();
    expect(screen.getByText("Просто нажми и спроси")).toBeTruthy();
    expect(screen.getByText("Не нужно печатать. Говори со мной как с другом.")).toBeTruthy();
    expect(screen.getByText("Попробуй сейчас")).toBeTruthy();
    expect(screen.getByText("Не сейчас")).toBeTruthy();
    await fireEvent.press(screen.getByTestId("how-to-chip-0"));
    expect(onAsk).toHaveBeenCalledWith("Найди мне дизель");
  });
});

describe("Russian strings", () => {
  test("every chip and line of the screen has a Russian entry, informal and with the drivers' words", () => {
    const keys = [
      ...HOW_TO_CHIPS.map((c) => c.text),
      "How to use",
      "How to talk to me",
      "Just tap and ask",
      "No typing. Talk to me like you'd talk to a friend.",
      "Tap me",
      "Say it your way",
      "I answer out loud",
      "Try saying",
      "Tap one and I ask it",
      "Needs Motive",
      "Try it now",
      "Not now",
      "All set. One thing before we drive.",
      "All set, {name}. One thing before we drive.",
    ];
    for (const k of keys) expect(RU[k]).toBeTruthy();
    expect(t("Find a CAT scale", undefined, "ru")).toBe("Найди кэт-скейл");
    expect(t("How many hours do I have?", undefined, "ru")).toBe("Сколько у меня осталось часов?");
    expect(t("How to talk to me", undefined, "ru")).toBe("Как со мной говорить");
    // "ты", never "вы"; "трак", never "грузовик".
    const ru = keys.map((k) => RU[k]).join(" ");
    expect(ru).not.toMatch(/(^|[\s,.])(вы|вас|вам|ваш)/i);
    expect(ru).not.toMatch(/грузовик/i);
  });
});

describe("how-to flag", () => {
  const map = { focused: true, micCard: false, busy: false, open: false };

  test("a driver onboarded before this version: never opened by itself, the dot is lit until seen", async () => {
    expect(await loadHowTo()).toBeNull();
    expect(shouldAutoShowHowTo(null, map)).toBe(false);
    expect(howToDot(null)).toBe(true);
    expect(howToDot(undefined)).toBe(false); // nothing while the key is read
  });

  test("after onboarding: shown once, and after a close never again, across restarts", async () => {
    await markHowToDue();
    expect(shouldAutoShowHowTo(await loadHowTo(), map)).toBe(true);
    await markHowToSeen();
    resetHowToForTests(); // the app restarts
    expect(await loadHowTo()).toBe("seen");
    expect(shouldAutoShowHowTo("seen", map)).toBe(false);
    expect(howToDot("seen")).toBe(false);
    await markHowToDue(); // onboarding run again later
    expect(await loadHowTo()).toBe("seen");
  });

  test("one thing at a time: not under another screen, over the mic card or while the copilot listens", () => {
    expect(shouldAutoShowHowTo("due", { ...map, focused: false })).toBe(false);
    expect(shouldAutoShowHowTo("due", { ...map, micCard: true })).toBe(false);
    expect(shouldAutoShowHowTo("due", { ...map, busy: true })).toBe(false);
    expect(shouldAutoShowHowTo("due", { ...map, open: true })).toBe(false);
  });
});

describe("useHowToFlow (the map's wiring)", () => {
  const flow = (o: Partial<Parameters<typeof useHowToFlow>[0]> = {}) => {
    const ask = jest.fn();
    const listen = jest.fn();
    return { ask, listen, opts: { focused: true, micCard: false, busy: false, ask, listen, ...o } };
  };

  test("right after onboarding it opens as the sheet once; the height card waits; a close ends it for good", async () => {
    await markHowToDue();
    const { opts } = flow();
    const h = await renderHook((p: typeof opts) => useHowToFlow(p), { initialProps: opts });
    await flush();
    expect(h.result.current.mode).toBe("first");
    expect(h.result.current.holdsCards).toBe(true);
    expect(h.result.current.dot).toBe(true);
    await act(async () => h.result.current.close());
    expect(h.result.current.mode).toBeNull();
    expect(h.result.current.holdsCards).toBe(false); // the height card comes now
    expect(h.result.current.dot).toBe(false);
    expect(await storage.getItem(HOW_TO_KEY, null)).toBe("seen");
    await h.rerender({ ...opts });
    expect(h.result.current.mode).toBeNull();
  });

  test("it waits while the mic card asks, then opens", async () => {
    await markHowToDue();
    const { opts } = flow({ micCard: true });
    const h = await renderHook((p: typeof opts) => useHowToFlow(p), { initialProps: opts });
    await flush();
    expect(h.result.current.mode).toBeNull();
    expect(h.result.current.holdsCards).toBe(true);
    await h.rerender({ ...opts, micCard: false });
    expect(h.result.current.mode).toBe("first");
  });

  test("a chip closes the screen and sends the phrase as a typed request", async () => {
    const { opts, ask, listen } = flow();
    const h = await renderHook(() => useHowToFlow(opts));
    await flush();
    expect(h.result.current.mode).toBeNull(); // an old driver: nothing opens by itself
    await act(async () => h.result.current.open());
    expect(h.result.current.mode).toBe("help");
    await act(async () => h.result.current.ask("Find me diesel"));
    expect(ask).toHaveBeenCalledWith("Find me diesel");
    expect(listen).not.toHaveBeenCalled();
    expect(h.result.current.mode).toBeNull();
    expect(h.result.current.dot).toBe(false);
  });

  test("Try it now closes the screen and starts listening", async () => {
    const { opts, ask, listen } = flow();
    const h = await renderHook(() => useHowToFlow(opts));
    await flush();
    await act(async () => h.result.current.open());
    await act(async () => h.result.current.tryNow());
    expect(listen).toHaveBeenCalledTimes(1);
    expect(ask).not.toHaveBeenCalled();
    expect(h.result.current.mode).toBeNull();
  });
});

describe("TopBar help button", () => {
  const bar = (helpDot: boolean, onHelpPress = noop) => (
    <TopBar
      topInset={0}
      truckLabel="13'6&quot;"
      hazmat={false}
      onSearch={() => true}
      onTruckPress={noop}
      onHelpPress={onHelpPress}
      helpDot={helpDot}
      onHistoryPress={noop}
      tester={false}
      onTesterPress={noop}
      isDemo={false}
      onLocationPress={noop}
      onLayout={noop}
    />
  );

  test("? sits between search and truck, opens the screen, and its dot goes out once seen", async () => {
    const onHelp = jest.fn();
    await render(bar(true, onHelp));
    expect(screen.getByLabelText("How to use")).toBeTruthy();
    expect(screen.getByTestId("help-dot")).toBeTruthy();
    await fireEvent.press(screen.getByTestId("help-button"));
    expect(onHelp).toHaveBeenCalledTimes(1);
    await render(bar(false));
    expect(screen.queryByTestId("help-dot")).toBeNull();
  });

  test("the dot follows the flag: lit for a driver who never opened it, out after any close", async () => {
    const h = await renderHook(() => useHowToFlow({ focused: true, micCard: false, busy: false, ask: noop, listen: noop }));
    await flush();
    await render(bar(h.result.current.dot));
    expect(screen.getByTestId("help-dot")).toBeTruthy();
    await act(async () => h.result.current.open());
    await act(async () => h.result.current.close());
    await render(bar(h.result.current.dot));
    expect(screen.queryByTestId("help-dot")).toBeNull();
  });

  test("the label is Russian for a Russian driver", async () => {
    useLangStore.setState({ lang: "ru" });
    await render(bar(true));
    expect(screen.getByLabelText("Как пользоваться")).toBeTruthy();
  });
});
