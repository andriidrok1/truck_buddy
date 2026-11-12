// The provider modules with their native sides mocked: what goes to Apple and Google, and what comes back to the app.
import { createHash } from "crypto";
import { Platform } from "react-native";

import { resetAppleForTests, signInWithApple } from "../signin/apple";
import { availableProviders, codeLinkAllowed, googleConfigured } from "../signin/available";
import { resetGoogleForTests, signInWithGoogle } from "../signin/google";

const mockApple = {
  available: true,
  signInAsync: jest.fn(),
};
jest.mock("expo-apple-authentication", () => ({
  isAvailableAsync: async () => mockApple.available,
  signInAsync: (o: unknown) => mockApple.signInAsync(o),
  AppleAuthenticationButton: () => null,
  AppleAuthenticationScope: { FULL_NAME: 0, EMAIL: 1 },
  AppleAuthenticationButtonType: { SIGN_IN: 0, CONTINUE: 1 },
  AppleAuthenticationButtonStyle: { WHITE: 0, WHITE_OUTLINE: 1, BLACK: 2 },
}));

// Real SHA-256 (node's), so the test checks the digest itself and not a stand-in; fixed bytes for the randomness.
const mockBytes = jest.fn((n: number) => Uint8Array.from({ length: n }, (_, i) => (i * 7 + 3) & 0xff));
jest.mock("expo-crypto", () => ({
  getRandomBytesAsync: async (n: number) => mockBytes(n),
  digestStringAsync: async (_alg: string, data: string) => jest.requireActual<typeof import("crypto")>("crypto").createHash("sha256").update(data).digest("hex"),
  CryptoDigestAlgorithm: { SHA256: "SHA-256" },
  CryptoEncoding: { HEX: "hex" },
}));

const mockGoogle = {
  configure: jest.fn(),
  hasPlayServices: jest.fn(async (_o?: unknown) => true),
  signIn: jest.fn(),
  getTokens: jest.fn(async () => ({ idToken: "from-getTokens", accessToken: "x" })),
  signOut: jest.fn(async () => null),
};
jest.mock("@react-native-google-signin/google-signin", () => ({
  GoogleSignin: {
    configure: (o: unknown) => mockGoogle.configure(o),
    hasPlayServices: (o: unknown) => mockGoogle.hasPlayServices(o),
    signIn: () => mockGoogle.signIn(),
    getTokens: () => mockGoogle.getTokens(),
    signOut: () => mockGoogle.signOut(),
  },
  statusCodes: { SIGN_IN_CANCELLED: "SIGN_IN_CANCELLED", IN_PROGRESS: "IN_PROGRESS", PLAY_SERVICES_NOT_AVAILABLE: "PLAY_SERVICES_NOT_AVAILABLE", SIGN_IN_REQUIRED: "SIGN_IN_REQUIRED" },
}));

const realOS = Platform.OS;
const setOS = (os: string) => Object.defineProperty(Platform, "OS", { configurable: true, get: () => os });
const WEB = "web-id.apps.googleusercontent.com";
const IOS = "ios-id.apps.googleusercontent.com";

beforeEach(() => {
  jest.clearAllMocks();
  mockApple.available = true;
  resetAppleForTests();
  resetGoogleForTests();
  setOS("ios");
  process.env.EXPO_PUBLIC_GOOGLE_WEB_CLIENT_ID = WEB;
  process.env.EXPO_PUBLIC_GOOGLE_IOS_CLIENT_ID = IOS;
});
afterAll(() => {
  setOS(realOS);
  delete process.env.EXPO_PUBLIC_GOOGLE_WEB_CLIENT_ID;
  delete process.env.EXPO_PUBLIC_GOOGLE_IOS_CLIENT_ID;
});

describe("Sign in with Apple", () => {
  const credential = (over: Record<string, unknown> = {}) => ({
    user: "apple-user",
    state: null,
    fullName: { givenName: "Mike", familyName: "Johnson", namePrefix: null, middleName: null, nameSuffix: null, nickname: null },
    email: "mike@privaterelay.appleid.com",
    realUserStatus: 2,
    identityToken: "apple.identity.token",
    authorizationCode: "auth-code",
    ...over,
  });

  test("Apple gets the SHA-256 of the nonce, the app keeps the raw one for the server; name and email are asked for", async () => {
    mockApple.signInAsync.mockResolvedValue(credential());
    const res = await signInWithApple();
    if (res.type !== "success") throw new Error("expected a credential");
    const { rawNonce } = res.credential;
    expect(rawNonce).toMatch(/^[0-9a-f]{64}$/); // 32 random bytes
    expect(mockBytes).toHaveBeenCalledWith(32);
    const sent = mockApple.signInAsync.mock.calls[0][0] as { nonce: string; requestedScopes: number[] };
    // The server computes sha256(raw).hexdigest() and compares it with the token's nonce claim (backend identity.py).
    expect(sent.nonce).toBe(createHash("sha256").update(rawNonce).digest("hex"));
    expect(sent.nonce).not.toBe(rawNonce);
    expect(sent.requestedScopes).toEqual([0, 1]); // FULL_NAME, EMAIL
    expect(res.credential).toEqual({ identityToken: "apple.identity.token", rawNonce, authorizationCode: "auth-code", name: "Mike Johnson" });
  });

  test("every sign-in gets its own nonce", async () => {
    mockApple.signInAsync.mockResolvedValue(credential());
    mockBytes.mockImplementationOnce((n) => Uint8Array.from({ length: n }, () => 1)).mockImplementationOnce((n) => Uint8Array.from({ length: n }, () => 2));
    const a = await signInWithApple();
    const b = await signInWithApple();
    if (a.type !== "success" || b.type !== "success") throw new Error("expected credentials");
    expect(a.credential.rawNonce).not.toBe(b.credential.rawNonce);
  });

  test("a later sign-in carries no name (Apple gives it once): null, not an empty string", async () => {
    mockApple.signInAsync.mockResolvedValue(credential({ fullName: { givenName: null, familyName: null }, email: null }));
    const res = await signInWithApple();
    expect(res.type === "success" && res.credential.name).toBeNull();
  });

  test("the driver closed the sheet: cancelled, not an error", async () => {
    mockApple.signInAsync.mockRejectedValue(Object.assign(new Error("The user canceled the authorization attempt"), { code: "ERR_REQUEST_CANCELED" }));
    await expect(signInWithApple()).resolves.toEqual({ type: "cancelled" });
  });

  test("anything else is an error", async () => {
    mockApple.signInAsync.mockRejectedValue(Object.assign(new Error("failed"), { code: "ERR_REQUEST_FAILED" }));
    await expect(signInWithApple()).rejects.toThrow("failed");
    mockApple.signInAsync.mockResolvedValue(credential({ identityToken: null }));
    await expect(signInWithApple()).rejects.toThrow("identity token");
  });

  test("Android has no Sign in with Apple: the module is never loaded", async () => {
    setOS("android");
    resetAppleForTests();
    await expect(signInWithApple()).rejects.toThrow("not available");
    expect(mockApple.signInAsync).not.toHaveBeenCalled();
  });
});

describe("Sign in with Google", () => {
  const success = (idToken: string | null = "google.id.token") => ({ type: "success", data: { idToken, serverAuthCode: null, scopes: [], user: { id: "1", name: "Mike", email: "m@example.com", photo: null, familyName: null, givenName: "Mike" } } });

  test("configured once with the web and iOS client ids; the id token comes back; Google's own session is closed", async () => {
    mockGoogle.signIn.mockResolvedValue(success());
    await expect(signInWithGoogle()).resolves.toEqual({ type: "success", idToken: "google.id.token" });
    await signInWithGoogle();
    expect(mockGoogle.configure).toHaveBeenCalledTimes(1);
    expect(mockGoogle.configure).toHaveBeenCalledWith({ webClientId: WEB, iosClientId: IOS });
    expect(mockGoogle.hasPlayServices).not.toHaveBeenCalled(); // iOS
    expect(mockGoogle.signOut).toHaveBeenCalledTimes(2); // the account chooser comes up next time
  });

  test("Android checks Google Play services first and needs only the web client id", async () => {
    setOS("android");
    delete process.env.EXPO_PUBLIC_GOOGLE_IOS_CLIENT_ID;
    mockGoogle.signIn.mockResolvedValue(success());
    await expect(signInWithGoogle()).resolves.toEqual({ type: "success", idToken: "google.id.token" });
    expect(mockGoogle.hasPlayServices).toHaveBeenCalledWith({ showPlayServicesUpdateDialog: true });
    expect(mockGoogle.configure).toHaveBeenCalledWith({ webClientId: WEB });
    mockGoogle.hasPlayServices.mockRejectedValueOnce(Object.assign(new Error("no play services"), { code: "PLAY_SERVICES_NOT_AVAILABLE" }));
    await expect(signInWithGoogle()).rejects.toThrow("no play services");
  });

  test("the driver closed the sheet, or tapped twice: cancelled, not an error", async () => {
    mockGoogle.signIn.mockResolvedValue({ type: "cancelled", data: null });
    await expect(signInWithGoogle()).resolves.toEqual({ type: "cancelled" });
    mockGoogle.signIn.mockRejectedValue(Object.assign(new Error("in progress"), { code: "IN_PROGRESS" }));
    await expect(signInWithGoogle()).resolves.toEqual({ type: "cancelled" });
    mockGoogle.signIn.mockRejectedValue(Object.assign(new Error("cancelled"), { code: "SIGN_IN_CANCELLED" }));
    await expect(signInWithGoogle()).resolves.toEqual({ type: "cancelled" });
  });

  test("no id token in the answer: asked for separately, and an error when there is none", async () => {
    mockGoogle.signIn.mockResolvedValue(success(null));
    await expect(signInWithGoogle()).resolves.toEqual({ type: "success", idToken: "from-getTokens" });
    mockGoogle.getTokens.mockResolvedValueOnce({ idToken: "", accessToken: "x" });
    await expect(signInWithGoogle()).rejects.toThrow("id token");
  });

  test("a build without the client ids says so and never touches the native module", async () => {
    delete process.env.EXPO_PUBLIC_GOOGLE_WEB_CLIENT_ID;
    await expect(signInWithGoogle()).resolves.toEqual({ type: "not_configured" });
    process.env.EXPO_PUBLIC_GOOGLE_WEB_CLIENT_ID = WEB;
    delete process.env.EXPO_PUBLIC_GOOGLE_IOS_CLIENT_ID; // iOS needs its own
    await expect(signInWithGoogle()).resolves.toEqual({ type: "not_configured" });
    expect(mockGoogle.configure).not.toHaveBeenCalled();
    expect(mockGoogle.signIn).not.toHaveBeenCalled();
  });
});

describe("which buttons a build shows", () => {
  const env = (platform: string, expoGo = false, dev = false) => ({ platform, expoGo, dev });

  test("an iPhone build: Apple and Google", async () => {
    await expect(availableProviders(env("ios"))).resolves.toEqual({ apple: true, google: true, googleConfigured: true });
  });

  test("an iPhone where the system has no Apple sign-in: Google only", async () => {
    mockApple.available = false;
    await expect(availableProviders(env("ios"))).resolves.toEqual({ apple: false, google: true, googleConfigured: true });
  });

  test("Android: Google only, and Apple's module is not even asked", async () => {
    setOS("android");
    resetAppleForTests();
    await expect(availableProviders(env("android"))).resolves.toEqual({ apple: false, google: true, googleConfigured: true });
  });

  test("Expo Go has neither", async () => {
    await expect(availableProviders(env("ios", true, true))).resolves.toEqual({ apple: false, google: false, googleConfigured: false });
    await expect(availableProviders(env("android", true, true))).resolves.toEqual({ apple: false, google: false, googleConfigured: false });
  });

  test("a build without Google client ids still shows the button, marked as not set up", async () => {
    delete process.env.EXPO_PUBLIC_GOOGLE_WEB_CLIENT_ID;
    await expect(availableProviders(env("ios"))).resolves.toEqual({ apple: true, google: true, googleConfigured: false });
    expect(googleConfigured("android", { webClientId: WEB, iosClientId: "" })).toBe(true);
    expect(googleConfigured("ios", { webClientId: WEB, iosClientId: "" })).toBe(false);
  });

  test("the access-code link: Expo Go and dev builds, never a store build", () => {
    expect(codeLinkAllowed(env("android", true, true))).toBe(true);
    expect(codeLinkAllowed(env("ios", false, true))).toBe(true);
    expect(codeLinkAllowed(env("ios", false, false))).toBe(false);
    expect(codeLinkAllowed(env("android", false, false))).toBe(false);
  });
});
