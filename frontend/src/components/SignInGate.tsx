import * as WebBrowser from "expo-web-browser";
import { useEffect, useRef, useState } from "react";
import { ActivityIndicator, Image, ScrollView, StyleSheet, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { codeRequested, requestCode, useAuthState } from "../auth";
import { PRIVACY_URL, TERMS_URL } from "../constants";
import { t } from "../i18n";
import { Text } from "../i18n/Text";
import { signInWith, type SignInOutcome } from "../signin";
import { loadApple } from "../signin/apple";
import { availableProviders, codeLinkAllowed, signInEnv, type Providers, type SignInEnv } from "../signin/available";
import { setSignInNotice, useSignInNotice, type Provider } from "../state/account";
import { fonts, radius, spacing, useTheme } from "../theme";
import { AccessGate } from "./AccessGate";
import { GoogleMark } from "./GoogleMark";
import { PressableScale } from "./PressableScale";

const FAILED = "Couldn't sign in. Check the connection and try again.";
const NOT_SET_UP = "Google sign-in isn't set up in this build.";
const BUTTON_H = 56;

type Props = {
  /** Where the app runs (Expo Go, a dev build, a store build). Tests pass it; the app reads it from the runtime. */
  env?: SignInEnv;
  /** The sign-in itself, for tests. */
  signIn?: (provider: Provider) => Promise<SignInOutcome>;
};

/**
 * Sign-in, after the intro and before consent (Figma f5Uha6gopQHRmcGlMXdCD9, B4). Required: there is no way past
 * it without an account, because the truck, the places and the trips are kept in the account and come back on a
 * new phone. Apple's button is the system one (iOS only), Google's follows their branding rules.
 *
 * States: idle; signing in (buttons off, a spinner, the title says so); failed (one line above the buttons, the
 * buttons work again). A provider's sheet closed by the driver is not a failure and shows nothing.
 *
 * Expo Go and dev builds also get "I have an access code" (the emulator stand signs in with a tester code there);
 * a build a driver gets never does.
 */
export function SignInGate({ env: envProp, signIn = signInWith }: Props) {
  const { colors, scheme } = useTheme();
  const insets = useSafeAreaInsets();
  const [env] = useState<SignInEnv>(() => envProp ?? signInEnv());
  const codeLink = codeLinkAllowed(env);
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

  const start = async (provider: Provider) => {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setError(null);
    let failed: string | null = null;
    try {
      const outcome = await signIn(provider);
      // Signed in: the root layout has already moved on. Cancelled: back to idle, nothing to say.
      if (outcome === "not_configured") failed = NOT_SET_UP;
      else if (outcome === "signed_in") setSignInNotice(null);
    } catch (e) {
      console.log(`[signin] ${provider} failed: ${String(e)}`);
      failed = FAILED;
    }
    busyRef.current = false;
    if (!alive.current) return;
    setBusy(false);
    setError(failed);
  };

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
        <Image source={require("../../assets/images/avatar.png")} style={[styles.avatar, { backgroundColor: colors.avatarBg }]} />
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
