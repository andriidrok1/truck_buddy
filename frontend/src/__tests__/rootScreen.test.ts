import { rootScreen, type RootScreen } from "../state/firstLaunch";

jest.mock("expo-secure-store", () => ({ getItemAsync: async () => null, setItemAsync: async () => {}, deleteItemAsync: async () => {} }));

type Consent = "loading" | "yes" | "no";
type Auth = "loading" | "in" | "out";

test("first launch goes language, the intro, sign-in, consent, then the app", () => {
  expect(rootScreen(false, false, "no", "out")).toBe("language");
  expect(rootScreen(true, false, "no", "out")).toBe("intro");
  expect(rootScreen(true, true, "no", "out")).toBe("signin");
  expect(rootScreen(true, true, "no", "in")).toBe("consent");
  expect(rootScreen(true, true, "yes", "in")).toBe("app");
});

test("a returning driver sees Welcome back once, after consent, and never before signing in", () => {
  expect(rootScreen(true, true, "no", "in", true)).toBe("consent");
  expect(rootScreen(true, true, "yes", "in", true)).toBe("welcome");
  expect(rootScreen(true, true, "yes", "in", false)).toBe("app");
  expect(rootScreen(true, true, "yes", "out", true)).toBe("signin");
});

test("signed out (the language, the intro and consent stay): straight to sign-in, then the app", () => {
  expect(rootScreen(true, true, "yes", "out")).toBe("signin");
  expect(rootScreen(true, true, "yes", "in")).toBe("app");
});

test("a driver who agreed to the consent screen before the intro existed never gets the intro", () => {
  expect(rootScreen(true, false, "yes", "out")).toBe("signin");
  expect(rootScreen(true, false, "yes", "in")).toBe("app");
});

test("every combination", () => {
  const want = (lang: boolean, intro: boolean, consent: Consent, auth: Auth, welcome: boolean): RootScreen => {
    if (!lang) return "language"; // first, whatever else is stored (a wipe that left other keys behind)
    if (!intro && consent !== "yes") return "intro";
    if (auth !== "in") return "signin"; // sign-in is required: nothing past it without a token
    if (consent !== "yes") return "consent";
    return welcome ? "welcome" : "app";
  };
  const seen = new Set<RootScreen>();
  let n = 0;
  for (const lang of [false, true])
    for (const intro of [false, true])
      for (const consent of ["loading", "yes", "no"] as Consent[])
        for (const auth of ["loading", "in", "out"] as Auth[])
          for (const welcome of [false, true]) {
            const got = rootScreen(lang, intro, consent, auth, welcome);
            expect({ lang, intro, consent, auth, welcome, got }).toEqual({ lang, intro, consent, auth, welcome, got: want(lang, intro, consent, auth, welcome) });
            // The invariants, said outright:
            if (got === "app" || got === "welcome" || got === "consent") expect(auth).toBe("in");
            if (got === "app" || got === "welcome") expect(consent).toBe("yes");
            seen.add(got);
            n++;
          }
  expect(n).toBe(72);
  expect([...seen].sort()).toEqual(["app", "consent", "intro", "language", "signin", "welcome"]);
});
