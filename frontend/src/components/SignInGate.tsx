import * as WebBrowser from "expo-web-browser";
import { useEffect, useRef, useState } from "react";
import { ActivityIndicator, AppState, Image, Pressable, ScrollView, StyleSheet, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { SignInError } from "../api";
import { codeRequested, requestCode, useAuthState } from "../auth";
import { PRIVACY_URL, TERMS_URL } from "../constants";
import { t } from "../i18n";
import { Text } from "../i18n/Text";
import { signInWith, type SignInOutcome, type SignInStage } from "../signin";
import { loadApple } from "../signin/apple";
import { availableProviders, codeLinkAllowed, signInEnv, type Providers, type SignInEnv } from "../signin/available";
import { setSignInNotice, useSignInNotice, type Provider } from "../state/account";
import { fonts, radius, spacing, useTheme } from "../theme";
import { AccessGate } from "./AccessGate";
import { GoogleMark } from "./GoogleMark";
import { PressableScale } from "./PressableScale";

const FAILED = "Couldn't sign in. Check the connection and try again.";
const REFUSED = "Sign-in was refused. Try again, or use the other button.";
const UNAVAILABLE = "Sign-in isn't available right now. Try again in a minute.";
const TOO_MANY = "Too many tries. Wait a few minutes.";
const NOT_SET_UP = "Google sign-in isn't set up in this build.";

/** What to tell the driver: no answer at all is the connection; the server's own "no" is said as what it is. */
export function signInMessage(e: unknown): string {
  if (!(e instanceof SignInError)) return FAILED; // the provider's sheet failed
  if (e.status === 401 || e.status === 403) return REFUSED;
  if (e.status === 429) return TOO_MANY;
  if (e.status >= 500) return UNAVAILABLE;
  return FAILED;
}

/** A provider sheet that never answers must not leave the buttons off for good. */
export const PROVIDER_TIMEOUT_MS = 60_000;
/** Back in the app with the sheet gone and still no answer after this long: the buttons come back. */
export const BACK_WITHOUT_ANSWER_MS = 3_000;
const TIMING = { provider: PROVIDER_TIMEOUT_MS, back: BACK_WITHOUT_ANSWER_MS };
/** How long the avatar is held to show the access-code link in a build that hides it. */
export const REVEAL_CODE_MS = 3_000;
const BUTTON_H = 56;

type Props = {
  /** Where the app runs (Expo Go, a dev build, a store build). Tests pass it; the app reads it from the runtime. */
  env?: SignInEnv;
  /** The sign-in itself, for tests. */
  signIn?: (provider: Provider, onStage?: (s: SignInStage) => void) => Promise<SignInOutcome>;
  /** The two waits, for tests (ms). */
  timing?: { provider: number; back: number };
};

/**
 * Sign-in, after the intro and before consent (Figma f5Uha6gopQHRmcGlMXdCD9, B4). Required: there is no way past
 * it without an account, because the truck, the places and the trips are kept in the account and come back on a
 * new phone. Apple's button is the system one (iOS only), Google's follows their branding rules.
 *
 * States: idle; signing in (buttons off, a spinner, the title says so); failed (one line above the buttons, the
 * buttons work again). A provider's sheet closed by the driver is not a failure and shows nothing.
 *
 * "I have an access code" (testers, the emulator stand, App Review, support) is visible at once in Expo Go and
 * dev builds. A build a driver gets shows it only after the avatar is held for 3 s: hidden on purpose, so there
 * is no hint for it.
 */
export function SignInGate({ env: envProp, signIn = signInWith, timing = TIMING }: Props) {
  const { colors, scheme } = useTheme();
  const insets = useSafeAreaInsets();
  const [env] = useState<SignInEnv>(() => envProp ?? signInEnv());
  const [revealed, setRevealed] = useState(false);
  const codeLink = codeLinkAllowed(env) || revealed;
  const [providers, setProviders] = useState<Providers | null>(null);
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false); // a second tap in the same frame, before the state lands
  const [error, setError] = useState<string | null>(null);
  // The code screen: the link below, or a tester who signed out and asked for it (truck profile). Only where the
  // link exists. useAuthState: the request can arrive after this screen is up.
  useAuthState();
  const [wantsCode, setWantsCode] = useState(false);
  const showCode = codeLink && (wantsCode || codeRequested());
  const notice = useSignInNotice();
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  useEffect(() => {
    let on = true;
    availableProviders(env)
      .catch((): Providers => ({ apple: false, google: false, googleConfigured: false }))
      .then((p) => {
        if (on) setProviders(p);
      });
    return () => {
      on = false;
    };
  }, [env]);

  // One attempt at a time; `run` tells a late answer of an abandoned attempt from the current one.
  const run = useRef(0);
  const stage = useRef<SignInStage | null>(null);
  const giveUp = useRef<ReturnType<typeof setTimeout> | null>(null);
  const idle = (attempt: number, failed: string | null) => {
    if (run.current !== attempt) return; // already given up on, or a newer attempt is running
    run.current++;
    stage.current = null;
    if (giveUp.current) clearTimeout(giveUp.current);
    giveUp.current = null;
    busyRef.current = false;
    if (!alive.current) return;
    setBusy(false);
    setError(failed);
  };

  const start = async (provider: Provider) => {
    if (busyRef.current) return;
    busyRef.current = true;
    const attempt = ++run.current;
    setBusy(true);
    setError(null);
    // The provider's sheet never came back with anything: the buttons work again. If its answer does arrive
    // later the sign-in still completes; it just no longer holds the screen.
    giveUp.current = setTimeout(() => idle(attempt, stage.current === "server" ? FAILED : null), timing.provider);
    let failed: string | null = null;
    try {
      const outcome = await signIn(provider, (s) => {
        if (run.current === attempt) stage.current = s;
      });
      // Signed in: the root layout has already moved on. Cancelled: back to idle, nothing to say.
      if (outcome === "not_configured") failed = NOT_SET_UP;
      else if (outcome === "signed_in") setSignInNotice(null);
    } catch (e) {
      console.log(`[signin] ${provider} failed: ${String(e)}`);
      failed = signInMessage(e);
    }
    idle(attempt, failed);
  };

  // The system sheet is gone (the app is active again) and the provider still has not answered: some SDKs never
  // settle a sheet dismissed by the system. Not while our own server is being asked; that call has its deadline.
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | null = null;
    const sub = AppState.addEventListener("change", (s) => {
      if (timer) clearTimeout(timer);
      timer = null;
      if (s !== "active" || !busyRef.current || stage.current !== "provider") return;
      const attempt = run.current;
      timer = setTimeout(() => {
        if (stage.current === "provider") idle(attempt, null);
      }, timing.back);
    });
    return () => {
      if (timer) clearTimeout(timer);
      if (giveUp.current) clearTimeout(giveUp.current);
      sub?.remove();
    };
  }, [timing.back]);

  if (showCode) {
    return (
      <AccessGate
        onSkip={() => {
          requestCode(false);
          setWantsCode(false);
        }}
      />
    );
  }

  const apple = providers?.apple ? loadApple() : null;
  const none = providers !== null && !apple && !providers.google;

  return (
    <View testID="signin-gate" style={[styles.root, { backgroundColor: colors.surface, paddingTop: insets.top, paddingBottom: insets.bottom + spacing.lg }]}>
      <ScrollView contentContainerStyle={styles.top} showsVerticalScrollIndicator={false}>
        {/* Held for 3 s: the access-code link appears (App Review, support). No label and no hint on purpose. */}
        <Pressable testID="signin-avatar" accessible={false} delayLongPress={REVEAL_CODE_MS} onLongPress={() => setRevealed(true)}>
          <Image source={require("../../assets/images/avatar.png")} style={[styles.avatar, { backgroundColor: colors.avatarBg }]} />
        </Pressable>
        <Text testID="signin-title" accessibilityRole="header" style={[styles.title, { color: colors.onSurface }]}>
          {busy ? "Signing you in…" : "Keep your truck, places and trips"}
        </Text>
        {busy ? (
          <ActivityIndicator testID="signin-busy" color={colors.brandPrimary} style={styles.spinner} />
        ) : (
          <Text style={[styles.sub, { color: colors.onSurfaceSecondary }]}>Sign in once. New phone or reinstall, everything is back.</Text>
        )}
      </ScrollView>

      <View style={styles.footer}>
        {!!error && (
          <Text testID="signin-error" accessibilityRole="alert" style={[styles.message, { color: colors.error }]}>
            {error}
          </Text>
        )}
        {!error && notice === "signed_out" && !busy && (
          <Text testID="signin-notice" accessibilityRole="alert" style={[styles.message, { color: colors.onSurface }]}>
            You were signed out. Sign in again.
          </Text>
        )}
        {none && (
          <Text testID="signin-unavailable" style={[styles.message, { color: colors.onSurfaceSecondary }]}>
            Sign-in with Apple or Google needs the installed app.
          </Text>
        )}
        {apple && (
          // The system button has no disabled state: while a sign-in runs it takes no touches and dims.
          <View testID="signin-apple-wrap" pointerEvents={busy ? "none" : "auto"} style={busy && styles.off}>
            <apple.AppleAuthenticationButton
              testID="signin-apple"
              buttonType={apple.AppleAuthenticationButtonType.CONTINUE}
              buttonStyle={scheme === "dark" ? apple.AppleAuthenticationButtonStyle.WHITE : apple.AppleAuthenticationButtonStyle.BLACK}
              cornerRadius={BUTTON_H / 2}
              style={styles.apple}
              onPress={() => start("apple")}
            />
          </View>
        )}
        {providers?.google && (
          <PressableScale
            testID="signin-google"
            accessibilityRole="button"
            accessibilityLabel={t("Continue with Google")}
            accessibilityState={{ disabled: busy, busy }}
            disabled={busy}
            onPress={() => start("google")}
            style={[styles.google, { backgroundColor: colors.googleButton, borderColor: colors.googleButtonBorder }, busy && styles.off]}
          >
            <GoogleMark />
            <Text style={[styles.googleText, { color: colors.onGoogleButton }]}>Continue with Google</Text>
          </PressableScale>
        )}

        <Text testID="signin-legal" style={[styles.legal, { color: colors.onSurfaceSecondary }]}>
          By continuing you agree to the Terms and the Privacy Policy. We only get your name and email.
        </Text>
        {/* The links sit in their own row, as on the consent screen: a word inside a sentence cannot be a 56 pt target. */}
        <View style={styles.links}>
          <PressableScale testID="signin-terms-link" accessibilityRole="link" onPress={() => WebBrowser.openBrowserAsync(TERMS_URL).catch(() => {})} style={styles.linkHit}>
            <Text style={[styles.link, { color: colors.info }]}>Terms of use</Text>
          </PressableScale>
          <PressableScale testID="signin-privacy-link" accessibilityRole="link" onPress={() => WebBrowser.openBrowserAsync(PRIVACY_URL).catch(() => {})} style={styles.linkHit}>
            <Text style={[styles.link, { color: colors.info }]}>Privacy policy</Text>
          </PressableScale>
        </View>
        {codeLink && (
          <PressableScale testID="signin-code" accessibilityRole="button" disabled={busy} onPress={() => setWantsCode(true)} style={styles.linkHit}>
            <Text style={[styles.link, { color: colors.info }]}>I have an access code</Text>
          </PressableScale>
        )}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, paddingHorizontal: spacing.xl },
  top: { flexGrow: 1, alignItems: "center", justifyContent: "center", paddingVertical: spacing.xl },
  avatar: { width: 132, height: 132, borderRadius: 66, marginBottom: spacing.xl },
  title: { fontFamily: fonts.extrabold, fontSize: 28, lineHeight: 34, textAlign: "center" },
  sub: { fontFamily: fonts.regular, fontSize: 17, lineHeight: 24, textAlign: "center", marginTop: spacing.md },
  spinner: { marginTop: spacing.xl },
  footer: { gap: spacing.md },
  message: { fontFamily: fonts.semibold, fontSize: 17, lineHeight: 23, textAlign: "center" },
  apple: { height: BUTTON_H, alignSelf: "stretch" },
  google: {
    height: BUTTON_H,
    borderRadius: radius.pill,
    borderWidth: 1,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: spacing.md,
  },
  googleText: { fontFamily: fonts.semibold, fontSize: 19 },
  off: { opacity: 0.5 },
  legal: { fontFamily: fonts.regular, fontSize: 12, lineHeight: 17, textAlign: "center", marginTop: spacing.xs },
  // Wraps to a second row when the language's words don't fit one (as on the consent screen).
  links: { flexDirection: "row", flexWrap: "wrap", justifyContent: "center", columnGap: spacing.xl },
  // Text links still get a 56 pt target (in-cab rule); the label stays a link, the area around it is the hit zone.
  linkHit: { minHeight: 56, minWidth: 56, maxWidth: "100%", alignItems: "center", justifyContent: "center", paddingHorizontal: spacing.sm },
  link: { fontFamily: fonts.semibold, fontSize: 15, textAlign: "center" },
});
