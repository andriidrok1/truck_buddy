// Review of #172: Android Back on the access-code screen, and the way out of the consent screen for someone who
// signed in with the wrong account.
import AsyncStorage from "@react-native-async-storage/async-storage";
import { act, fireEvent, render, renderHook, screen } from "@testing-library/react-native";
import { BackHandler } from "react-native";

import { codeRequested, getToken, loadToken, requestCode, setToken, useAuthState } from "../auth";
import { AccessGate } from "../components/AccessGate";
import { ConsentGate, signOutFromConsent } from "../components/ConsentGate";
import { giveConsent, useConsent } from "../consent";
import { t, useLangStore } from "../i18n";
import { RU } from "../i18n/ru";
import { rootScreen, resetToFirstLaunch } from "../state/firstLaunch";
import { markIntroSeen, loadIntro } from "../state/intro";
import { __resetWipeGate } from "../utils/storage/pendingWipe";

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
jest.mock("react-native-safe-area-context", () =>({ useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }) }));
jest.mock("@/src/keyboard", () => {
  const { ScrollView } = jest.requireActual<typeof import("react-native")>("react-native");
  return { KeyboardAwareScrollView: ScrollView };
});
jest.mock("../components/Icon", () => {
  const { Text } = jest.requireActual<typeof import("react-native")>("react-native");
  return { Icon: ({ name }: { name: string }) => <Text>{`icon:${name}`}</Text> };
});
jest.mock("expo-web-browser", () => ({ openBrowserAsync: jest.fn(() => Promise.resolve({ type: "opened" })) }));
jest.mock("expo-location", () => ({ requestForegroundPermissionsAsync: jest.fn(async () => ({ status: "granted" })) }));
jest.mock("../geo/location", () => ({ startLocation: jest.fn() }));
const mockSecure = new Map<string, string>();
jest.mock("expo-secure-store", () => ({
  getItemAsync: async (k: string) => mockSecure.get(k) ?? null,
  setItemAsync: async (k: string, v: string) => void mockSecure.set(k, v),
  deleteItemAsync: async (k: string) => void mockSecure.delete(k),
}));

const realFetch = global.fetch;
beforeEach(async () => {
  global.fetch = jest.fn(async () => new Response("{}", { status: 500 })) as typeof fetch; // nothing reaches a server
  await AsyncStorage.clear();
  mockSecure.clear();
  __resetWipeGate();
  await loadToken();
  requestCode(false);
  useLangStore.setState({ lang: "en", chosen: true });
});
afterEach(async () => {
  await act(async () => {
    await resetToFirstLaunch();
  });
  global.fetch = realFetch;
});

describe("Android Back on the access-code screen", () => {
  type Handler = () => boolean | null | undefined;
  let handlers: Handler[];
  let spy: jest.SpyInstance;
  beforeEach(() => {
    handlers = [];
    spy = jest.spyOn(BackHandler, "addEventListener").mockImplementation((_event, handler) => {
      handlers.push(handler as Handler);
      return { remove: () => void (handlers = handlers.filter((h) => h !== handler)) };
    });
  });
  afterEach(() => spy.mockRestore());
  const pressBack = async () => {
    let handled: unknown = false;
    await act(async () => {
      handled = handlers.length ? handlers[handlers.length - 1]() : false;
    });
    return handled === true;
  };

  test("goes back to the sign-in screen instead of sending the app to the background", async () => {
    const onSkip = jest.fn();
    await render(<AccessGate onSkip={onSkip} />);
    expect(await pressBack()).toBe(true);
    expect(onSkip).toHaveBeenCalledTimes(1);
  });

  test("while a code is being checked the press is swallowed; it works again once the answer is in", async () => {
    let answer: (r: Response) => void = () => {};
    global.fetch = jest.fn(() => new Promise<Response>((r) => (answer = r))) as typeof fetch;
    const onSkip = jest.fn();
    await render(<AccessGate onSkip={onSkip} />);
    await fireEvent.changeText(screen.getByTestId("access-code-input"), "WRONG");
    await fireEvent.press(screen.getByTestId("access-code-submit"));
    expect(await pressBack()).toBe(true);
    expect(onSkip).not.toHaveBeenCalled();
    await act(async () => {
      answer(new Response("{}", { status: 401 }));
    });
    expect(screen.getByTestId("access-code-error")).toBeTruthy();
    expect(await pressBack()).toBe(true);
    expect(onSkip).toHaveBeenCalledTimes(1);
  });

  test("with nothing to go back to, the system has it", async () => {
    await render(<AccessGate />);
    expect(handlers).toHaveLength(0);
  });

  test("the listener goes with the screen", async () => {
    const view = await render(<AccessGate onSkip={() => {}} />);
    expect(handlers).toHaveLength(1);
    await view.unmount();
    expect(handlers).toHaveLength(0);
  });
});

describe("Sign out on the consent screen", () => {
  test("a secondary action under the button runs the sign-out it is given", async () => {
    const signOut = jest.fn(async () => {});
    await render(<ConsentGate signOut={signOut} />);
    expect(screen.getByText("Sign out")).toBeTruthy();
    await fireEvent.press(screen.getByTestId("consent-sign-out"));
    expect(signOut).toHaveBeenCalledTimes(1);
  });

  test("one action at a time: while the sign-out runs, Agree does nothing", async () => {
    let done: () => void = () => {};
    const signOut = jest.fn(() => new Promise<void>((r) => (done = r)));
    await render(<ConsentGate signOut={signOut} />);
    await fireEvent.press(screen.getByTestId("consent-sign-out"));
    await fireEvent.press(screen.getByTestId("consent-agree"));
    await fireEvent.press(screen.getByTestId("consent-sign-out"));
    expect(signOut).toHaveBeenCalledTimes(1);
    const consent = await renderHook(() => useConsent());
    expect(consent.result.current).toBe("no");
    await act(async () => done());
  });

  test("Russian: the label is in the catalog", () => {
    expect(RU["Sign out"]).toBe("Выйти");
    useLangStore.setState({ lang: "ru", chosen: true });
    expect(t("Sign out")).toBe("Выйти");
  });

  test("an account: the phone signs out, the root shows the sign-in screen, the next driver gets the consent screen", async () => {
    await markIntroSeen();
    await setToken("ACC-WRONG", "driver", "account", "REF-WRONG");
    const consent = await renderHook(() => useConsent());
    const auth = await renderHook(() => useAuthState());
    expect(rootScreen(true, await loadIntro(), consent.result.current, auth.result.current)).toBe("consent");
    await act(async () => {
      await signOutFromConsent();
    });
    await auth.rerender({});
    expect(getToken()).toBeNull();
    expect(codeRequested()).toBe(false);
    expect(rootScreen(true, await loadIntro(), consent.result.current, auth.result.current)).toBe("signin");
  });

  test("an access code: back to the code screen, as the truck screen's Sign out does", async () => {
    await setToken("CODE-TOKEN", "tester", "code", null);
    await act(async () => {
      await signOutFromConsent();
    });
    expect(getToken()).toBeNull();
    expect(codeRequested()).toBe(true);
  });

  test("a consent given before the sign-out is gone after it", async () => {
    await setToken("ACC-A", "driver", "account", "REF-A");
    await giveConsent();
    const consent = await renderHook(() => useConsent());
    expect(consent.result.current).toBe("yes");
    await act(async () => {
      await signOutFromConsent();
    });
    await consent.rerender({});
    expect(consent.result.current).toBe("no");
    expect(await AsyncStorage.getItem("ai_consent_v2")).toBeNull();
  });
});
