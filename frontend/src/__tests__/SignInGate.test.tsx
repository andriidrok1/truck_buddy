// The sign-in screen (Figma f5Uha6gopQHRmcGlMXdCD9, B4) from the button to the stored token. The native sides of
// Apple and Google are mocked here, so none of this proves a real sign-in on a phone.
import AsyncStorage from "@react-native-async-storage/async-storage";
import { createHash } from "crypto";
import { act, fireEvent, render, screen } from "@testing-library/react-native";
import * as WebBrowser from "expo-web-browser";

import { getRefreshToken, getToken, requestCode, setToken, signedInVia } from "../auth";
import { SignInGate } from "../components/SignInGate";
import { PRIVACY_URL, TERMS_URL } from "../constants";
import { forgetTruckProfile, isOnboarded } from "../hooks/useTruckProfile";
import { t, useLangStore } from "../i18n";
import { RU } from "../i18n/ru";
import { resetAppleForTests } from "../signin/apple";
import type { SignInEnv } from "../signin/available";
import { resetGoogleForTests } from "../signin/google";
import { getAccount, resetAccountForTests, setSignInNotice, welcomeBackDue } from "../state/account";
import { forgetHowTo, loadHowTo } from "../state/howTo";

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
jest.mock("expo-web-browser", () => ({ openBrowserAsync: jest.fn(() => Promise.resolve({ type: "opened" })) }));
const mockSecure = new Map<string, string>();
jest.mock("expo-secure-store", () => ({
  getItemAsync: async (k: string) => mockSecure.get(k) ?? null,
  setItemAsync: async (k: string, v: string) => void mockSecure.set(k, v),
  deleteItemAsync: async (k: string) => void mockSecure.delete(k),
}));
// The code screen itself has its own behaviour; here only whether it is shown, and the way back.
jest.mock("../components/AccessGate", () => {
  const { Pressable, Text } = jest.requireActual<typeof import("react-native")>("react-native");
  return {
    AccessGate: ({ onSkip }: { onSkip?: () => void }) => (
      <Pressable testID="access-gate" onPress={onSkip}>
        <Text>code screen</Text>
      </Pressable>
    ),
  };
});

const mockApple = { available: true, signInAsync: jest.fn(), buttonProps: [] as Record<string, unknown>[] };
jest.mock("expo-apple-authentication", () => {
  const { Pressable } = jest.requireActual<typeof import("react-native")>("react-native");
  return {
    isAvailableAsync: async () => mockApple.available,
    signInAsync: (o: unknown) => mockApple.signInAsync(o),
    // The system button, as a plain pressable that remembers what it was asked to look like.
    AppleAuthenticationButton: (p: Record<string, unknown> & { onPress: () => void; testID?: string }) => {
      mockApple.buttonProps.push(p);
      return <Pressable testID={p.testID} onPress={p.onPress} />;
    },
    AppleAuthenticationScope: { FULL_NAME: 0, EMAIL: 1 },
    AppleAuthenticationButtonType: { SIGN_IN: 0, CONTINUE: 1 },
    AppleAuthenticationButtonStyle: { WHITE: 0, WHITE_OUTLINE: 1, BLACK: 2 },
  };
});
jest.mock("expo-crypto", () => ({
  getRandomBytesAsync: async (n: number) => Uint8Array.from({ length: n }, (_, i) => (i * 5 + 1) & 0xff),
  digestStringAsync: async (_alg: string, data: string) => jest.requireActual<typeof import("crypto")>("crypto").createHash("sha256").update(data).digest("hex"),
  CryptoDigestAlgorithm: { SHA256: "SHA-256" },
  CryptoEncoding: { HEX: "hex" },
}));
const mockGoogle = { signIn: jest.fn(), configure: jest.fn() };
jest.mock("@react-native-google-signin/google-signin", () => ({
  GoogleSignin: {
    configure: (o: unknown) => mockGoogle.configure(o),
    hasPlayServices: async () => true,
    signIn: () => mockGoogle.signIn(),
    getTokens: async () => ({ idToken: "", accessToken: "" }),
    signOut: async () => null,
  },
  statusCodes: { SIGN_IN_CANCELLED: "SIGN_IN_CANCELLED", IN_PROGRESS: "IN_PROGRESS", PLAY_SERVICES_NOT_AVAILABLE: "PLAY_SERVICES_NOT_AVAILABLE", SIGN_IN_REQUIRED: "SIGN_IN_REQUIRED" },
}));

const STORE: SignInEnv = { platform: "ios", expoGo: false, dev: false };
const DEV: SignInEnv = { platform: "ios", expoGo: false, dev: true };
const EXPO_GO: SignInEnv = { platform: "android", expoGo: true, dev: true };

const FAILED = "Couldn't sign in. Check the connection and try again.";
const TITLE = "Keep your truck, places and trips";

type Call = { path: string; body: Record<string, unknown>; headers: Record<string, string> };
let calls: Call[] = [];
const realFetch = global.fetch;
const answer = (status: number, body: unknown = {}) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const account = (over: Record<string, unknown> = {}) => ({
  access_token: "ACC-1",
  refresh_token: "REF-1",
  token_type: "bearer",
  role: "driver",
  expires_in: 2592000,
  new_account: true,
  has_profile: false,
  name: "Mike Johnson",
  email: "mike@example.com",
  ...over,
});
/** The server: one reply per sign-in call, in order (the last repeats). A function reply decides when to answer. */
function server(...replies: (Response | "offline" | (() => Promise<Response>))[]) {
  calls = [];
  const queue = [...replies];
  global.fetch = jest.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ path: String(url).replace("https://ci.invalid/api", ""), body: JSON.parse(String(init?.body ?? "{}")), headers: (init?.headers ?? {}) as Record<string, string> });
    const r = queue.length > 1 ? queue.shift()! : queue[0];
    if (r === "offline") throw new TypeError("Network request failed");
    return typeof r === "function" ? r() : r.clone();
  }) as typeof fetch;
}
const appleCredential = (over: Record<string, unknown> = {}) => ({
  user: "u1",
  state: null,
  fullName: { givenName: "Mike", familyName: "Johnson" },
  email: "mike@example.com",
  realUserStatus: 2,
  identityToken: "apple.identity.token",
  authorizationCode: "apple-auth-code",
  ...over,
});
const googleUser = { type: "success", data: { idToken: "google.id.token", serverAuthCode: null, scopes: [], user: { id: "1", name: "Mike", email: "m@example.com", photo: null, familyName: null, givenName: "Mike" } } };

const settle = async () => {
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });
};
/** The screen with its buttons in (which ones exist is found out after the first render). */
async function show(env: SignInEnv = STORE) {
  await render(<SignInGate env={env} />);
  await settle();
}
const disabled = (id: string) => screen.getByTestId(id).props.accessibilityState?.disabled === true;

beforeEach(async () => {
  await AsyncStorage.clear();
  mockSecure.clear();
  mockApple.available = true;
  mockApple.signInAsync.mockReset();
  mockApple.buttonProps = [];
  mockGoogle.signIn.mockReset();
  mockGoogle.configure.mockReset();
  jest.mocked(WebBrowser.openBrowserAsync).mockClear();
  resetAppleForTests();
  resetGoogleForTests();
  resetAccountForTests();
  requestCode(false);
  await setToken(null);
  await forgetTruckProfile();
  await forgetHowTo();
  useLangStore.setState({ lang: "en", chosen: true, loaded: true });
  process.env.EXPO_PUBLIC_GOOGLE_WEB_CLIENT_ID = "web-id.apps.googleusercontent.com";
  process.env.EXPO_PUBLIC_GOOGLE_IOS_CLIENT_ID = "ios-id.apps.googleusercontent.com";
  server(answer(200, account()));
});
afterAll(() => {
  global.fetch = realFetch;
  delete process.env.EXPO_PUBLIC_GOOGLE_WEB_CLIENT_ID;
  delete process.env.EXPO_PUBLIC_GOOGLE_IOS_CLIENT_ID;
});

describe("SignInGate", () => {
  test("idle: the promise, both buttons, the legal line with its two links, and no way past without signing in", async () => {
    await show();
    expect(screen.getByText(TITLE)).toBeTruthy();
    expect(screen.getByText("Sign in once. New phone or reinstall, everything is back.")).toBeTruthy();
    expect(screen.getByTestId("signin-apple")).toBeTruthy();
    expect(screen.getByText("Continue with Google")).toBeTruthy();
    expect(screen.getByText("By continuing you agree to the Terms and the Privacy Policy. We only get your name and email.")).toBeTruthy();
    expect(screen.queryByText(/not now/i)).toBeNull();
    expect(screen.queryByText(/skip/i)).toBeNull();
    expect(screen.queryByTestId("signin-error")).toBeNull();
    expect(screen.queryByTestId("signin-notice")).toBeNull();
    expect(screen.queryByTestId("signin-busy")).toBeNull();
    // Apple's own button: "Continue with Apple", black on the light theme, 56 high, a pill.
    const apple = mockApple.buttonProps[mockApple.buttonProps.length - 1];
    expect(apple).toMatchObject({ buttonType: 1, buttonStyle: 2, cornerRadius: 28 });
    expect(apple.style).toMatchObject({ height: 56 });

    await fireEvent.press(screen.getByTestId("signin-terms-link"));
    await fireEvent.press(screen.getByTestId("signin-privacy-link"));
    expect(jest.mocked(WebBrowser.openBrowserAsync).mock.calls.map((c) => c[0])).toEqual([TERMS_URL, PRIVACY_URL]);
  });

  test("Apple, a new account: the identity token and the raw nonce go to the server, the token pair is stored as an account sign-in, onboarding is still ahead", async () => {
    mockApple.signInAsync.mockResolvedValue(appleCredential());
    await show();
    await fireEvent.press(screen.getByTestId("signin-apple"));
    await settle();

    expect(calls).toHaveLength(1);
    expect(calls[0].path).toBe("/auth/apple");
    const { nonce, ...rest } = calls[0].body as { nonce: string };
    expect(rest).toEqual({ identity_token: "apple.identity.token", authorization_code: "apple-auth-code", name: "Mike Johnson", platform: "ios" });
    // Apple got the hash, the server gets the raw value it can hash itself.
    const toApple = (mockApple.signInAsync.mock.calls[0][0] as { nonce: string }).nonce;
    expect(nonce).toMatch(/^[0-9a-f]{64}$/);
    expect(toApple).toBe(createHash("sha256").update(nonce).digest("hex"));
    expect(calls[0].headers.Authorization).toBeUndefined();

    expect(getToken()).toBe("ACC-1");
    expect(getRefreshToken()).toBe("REF-1");
    expect(signedInVia()).toBe("account");
    expect(JSON.parse(mockSecure.get("auth_via")!)).toBe("account");
    expect(getAccount()).toEqual({ name: "Mike Johnson", email: "mike@example.com", provider: "apple" });
    expect(await isOnboarded()).toBe(false);
    expect(welcomeBackDue()).toBe(false);
  });

  test("Google, a returning driver: the id token goes to the server; onboarding is skipped and Welcome back is due", async () => {
    mockGoogle.signIn.mockResolvedValue(googleUser);
    server(answer(200, account({ new_account: false, has_profile: true, access_token: "ACC-2", refresh_token: "REF-2" })));
    await show();
    await fireEvent.press(screen.getByTestId("signin-google"));
    await settle();

    expect(calls.map((c) => c.path)).toEqual(["/auth/google"]);
    expect(calls[0].body).toEqual({ id_token: "google.id.token", platform: "ios" });
    expect(getToken()).toBe("ACC-2");
    expect(signedInVia()).toBe("account");
    expect(getAccount()).toMatchObject({ provider: "google", email: "mike@example.com" });
    expect(await isOnboarded()).toBe(true);
    expect(await loadHowTo()).toBe("seen"); // the screen does not open by itself for a driver who knows the app
    expect(welcomeBackDue()).toBe(true);
  });

  test("the sheet was closed: no error, no request, the buttons work again", async () => {
    mockApple.signInAsync.mockRejectedValue(Object.assign(new Error("canceled"), { code: "ERR_REQUEST_CANCELED" }));
    mockGoogle.signIn.mockResolvedValue({ type: "cancelled", data: null });
    await show();
    await fireEvent.press(screen.getByTestId("signin-apple"));
    await settle();
    await fireEvent.press(screen.getByTestId("signin-google"));
    await settle();
    expect(calls).toHaveLength(0);
    expect(screen.queryByTestId("signin-error")).toBeNull();
    expect(screen.getByText(TITLE)).toBeTruthy();
    expect(disabled("signin-google")).toBe(false);
    expect(getToken()).toBeNull();
  });

  test("no connection: the error above the buttons, the buttons work again, and the next try can succeed", async () => {
    mockGoogle.signIn.mockResolvedValue(googleUser);
    server("offline", answer(200, account()));
    await show();
    await fireEvent.press(screen.getByTestId("signin-google"));
    await settle();
    expect(screen.getByTestId("signin-error").props.children).toBe(FAILED);
    expect(screen.getByText(TITLE)).toBeTruthy();
    expect(screen.queryByTestId("signin-busy")).toBeNull();
    expect(disabled("signin-google")).toBe(false);
    expect(getToken()).toBeNull();
    expect(getAccount()).toBeNull();

    await fireEvent.press(screen.getByTestId("signin-google"));
    await settle();
    expect(calls).toHaveLength(2);
    expect(getToken()).toBe("ACC-1");
  });

  test("the server says no (401, 429, 503): the same error, nothing stored", async () => {
    mockGoogle.signIn.mockResolvedValue(googleUser);
    await show();
    for (const status of [401, 429, 503]) {
      server(answer(status, { detail: "Sign-in failed" }));
      await fireEvent.press(screen.getByTestId("signin-google"));
      await settle();
      expect(screen.getByTestId("signin-error").props.children).toBe(FAILED);
      expect(getToken()).toBeNull();
    }
    expect(await isOnboarded()).toBe(false);
  });

  test("a provider that fails outright shows the error too", async () => {
    mockApple.signInAsync.mockRejectedValue(Object.assign(new Error("unknown"), { code: "ERR_REQUEST_UNKNOWN" }));
    await show();
    await fireEvent.press(screen.getByTestId("signin-apple"));
    await settle();
    expect(screen.getByTestId("signin-error").props.children).toBe(FAILED);
    expect(calls).toHaveLength(0);
  });

  test("signing in: the title says so, a spinner shows, both buttons are off, and a double tap sends one request", async () => {
    mockGoogle.signIn.mockResolvedValue(googleUser);
    let finish: (r: Response) => void = () => {};
    server(() => new Promise<Response>((resolve) => (finish = resolve)));
    await show();
    await fireEvent.press(screen.getByTestId("signin-google"));
    await fireEvent.press(screen.getByTestId("signin-google"));
    await settle();

    expect(screen.getByText("Signing you in…")).toBeTruthy();
    expect(screen.queryByText(TITLE)).toBeNull();
    expect(screen.getByTestId("signin-busy")).toBeTruthy();
    expect(disabled("signin-google")).toBe(true);
    // The system Apple button has no disabled prop: the view around it stops taking touches, and a press that got
    // through anyway (as this one does under jest) starts nothing.
    expect(screen.getByTestId("signin-apple-wrap").props.pointerEvents).toBe("none");
    await fireEvent.press(screen.getByTestId("signin-apple"));
    await settle();
    expect(mockGoogle.signIn).toHaveBeenCalledTimes(1);
    expect(mockApple.signInAsync).not.toHaveBeenCalled();
    expect(calls).toHaveLength(1);

    await act(async () => {
      finish(answer(200, account()));
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(calls).toHaveLength(1);
    expect(getToken()).toBe("ACC-1");
  });

  test("a build without Google client ids: the button says it is not set up instead of crashing", async () => {
    delete process.env.EXPO_PUBLIC_GOOGLE_WEB_CLIENT_ID;
    await show();
    await fireEvent.press(screen.getByTestId("signin-google"));
    await settle();
    expect(screen.getByTestId("signin-error").props.children).toBe("Google sign-in isn't set up in this build.");
    expect(mockGoogle.signIn).not.toHaveBeenCalled();
    expect(calls).toHaveLength(0);
  });

  test("an iPhone without Apple sign-in, and Android: Google only", async () => {
    mockApple.available = false;
    await show();
    expect(screen.queryByTestId("signin-apple")).toBeNull();
    expect(screen.getByTestId("signin-google")).toBeTruthy();
    expect(screen.queryByTestId("signin-unavailable")).toBeNull();
  });

  test("Expo Go: neither provider exists, the screen says so and offers the access code", async () => {
    await show(EXPO_GO);
    expect(screen.queryByTestId("signin-apple")).toBeNull();
    expect(screen.queryByTestId("signin-google")).toBeNull();
    expect(screen.getByTestId("signin-unavailable")).toBeTruthy();
    await fireEvent.press(screen.getByTestId("signin-code"));
    expect(screen.getByTestId("access-gate")).toBeTruthy();
    expect(screen.queryByTestId("signin-gate")).toBeNull();
    await fireEvent.press(screen.getByTestId("access-gate")); // "Back to sign-in"
    expect(screen.getByTestId("signin-gate")).toBeTruthy();
  });

  test("a dev build has the code link next to the real buttons", async () => {
    await show(DEV);
    expect(screen.getByTestId("signin-code")).toBeTruthy();
    expect(screen.getByTestId("signin-apple")).toBeTruthy();
    expect(screen.getByTestId("signin-google")).toBeTruthy();
  });

  test("a store build never has the code link or the code screen", async () => {
    requestCode(); // even a tester who asked for the code screen gets none
    await show(STORE);
    expect(screen.queryByTestId("signin-code")).toBeNull();
    expect(screen.queryByTestId("access-gate")).toBeNull();
    expect(screen.queryByText("I have an access code")).toBeNull();
    expect(screen.getByTestId("signin-gate")).toBeTruthy();
  });

  test("a tester who signed out lands on the code screen in a dev build", async () => {
    await show(DEV);
    await act(async () => requestCode()); // arrives after the screen is up (truck profile, after the reset)
    expect(screen.getByTestId("access-gate")).toBeTruthy();
  });

  test("the server turned the token down: one line says so, and it goes once a sign-in starts", async () => {
    setSignInNotice("signed_out");
    mockGoogle.signIn.mockResolvedValue({ type: "cancelled", data: null });
    await show();
    expect(screen.getByTestId("signin-notice").props.children).toBe("You were signed out. Sign in again.");
    mockGoogle.signIn.mockResolvedValue(googleUser);
    server("offline");
    await fireEvent.press(screen.getByTestId("signin-google"));
    await settle();
    expect(screen.queryByTestId("signin-notice")).toBeNull(); // the error has the line now
    expect(screen.getByTestId("signin-error")).toBeTruthy();
  });

  test("dark theme: Apple's button is the white one", async () => {
    const theme = jest.requireActual<typeof import("../theme")>("../theme");
    await act(async () => theme.setThemePref("dark"));
    await show();
    expect(mockApple.buttonProps[mockApple.buttonProps.length - 1]).toMatchObject({ buttonStyle: 0 });
    await act(async () => theme.setThemePref("light"));
  });

  test("in Russian: every line of the screen, informal and without gendered forms", async () => {
    useLangStore.setState({ lang: "ru" });
    setSignInNotice("signed_out");
    await show(DEV);
    for (const en of [
      TITLE,
      "Sign in once. New phone or reinstall, everything is back.",
      "Continue with Google",
      "By continuing you agree to the Terms and the Privacy Policy. We only get your name and email.",
      "You were signed out. Sign in again.",
      "I have an access code",
      "Terms of use",
      "Privacy policy",
    ]) {
      expect(RU[en]).toBeTruthy();
      expect(screen.getByText(RU[en])).toBeTruthy();
    }
    expect(screen.getByTestId("signin-google").props.accessibilityLabel).toBe("Продолжить с Google");
    for (const en of ["Signing you in…", FAILED, "Google sign-in isn't set up in this build.", "Sign-in with Apple or Google needs the installed app.", "Back to sign-in", "Welcome back", "Here is what I brought over.", "Open the map"]) {
      expect(t(en, undefined, "ru")).not.toBe(en);
    }
    expect(t("Welcome back, Mike", undefined, "ru")).toBe("С возвращением, Mike");
    // No past-tense verb that names the driver's gender ("вышел", "вошла") in the new block.
    const block = Object.entries(RU).filter(([k]) => [TITLE, FAILED, "You were signed out. Sign in again.", "Here is what I brought over.", "Signing you in…"].includes(k));
    for (const [, ru] of block) expect(ru).not.toMatch(/(вышел|вышла|вошёл|вошла|перенёс|перенесла|готов\b|готова\b)/i);
  });
});
