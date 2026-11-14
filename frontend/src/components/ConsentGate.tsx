import * as Location from "expo-location";
import * as WebBrowser from "expo-web-browser";
import { useEffect, useRef, useState } from "react";
import { ActivityIndicator, Image, Platform, ScrollView, StyleSheet, View } from "react-native";
import { Text } from "../i18n/Text";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { requestCode, signedInWithCode } from "../auth";
import { PRIVACY_URL, TERMS_URL } from "../constants";
import { giveConsent } from "../consent";
import { fonts, radius, spacing, useTheme } from "../theme";
import { startLocation } from "../geo/location";
import { signedOutReset } from "../state/firstLaunch";
import { Icon } from "./Icon";
import { PressableScale } from "./PressableScale";
import { t } from "../i18n";

const ITEMS: { icon: string; title: string; body: string }[] = [
  {
    icon: "microphone",
    title: "Your voice and requests go to OpenAI",
    body: "When you talk to TruckBuddy, the recording, its transcript, your position, truck size and recent requests are sent to OpenAI to understand you and speak the answer. Satellite images of a destination go there too, to check the parking.",
  },
  {
    icon: "map-marker",
    title: "Your location goes to map services",
    body: "Your position and truck size are sent to map services to find stops and plan truck-safe routes. When you ask what drivers say about a place, its name and location go to Google Gemini.",
  },
  {
    icon: "alert-outline",
    title: "Road signs come first",
    body: "Bridge heights, truck restrictions and parking can be missing or wrong. Posted signs and the law always win over the app, and you decide what is safe for your truck.",
  },
  {
    icon: "shield-check",
    title: "Nothing is sold or used for ads",
    body: "These services only process your request. TruckBuddy doesn't track you across apps.",
  },
];

/** The normal sign-out, as on the truck screen: an access-code sign-in goes back to the code screen. */
export async function signOutFromConsent(): Promise<void> {
  const code = signedInWithCode(); // before the reset: it forgets how the phone signed in
  await signedOutReset();
  if (code) requestCode();
}

/** Shown once the driver has signed in, before the app (again after a sign-out, "Delete my data" or a new consent
 * version, src/consent.ts): the driver agrees to sharing voice and location with third-party services. "Sign out"
 * under the button is the way back for someone who signed in with the wrong account (review of #172). */
export function ConsentGate({ signOut = signOutFromConsent }: { signOut?: () => Promise<void> } = {}) {
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const [busy, setBusy] = useState<"agree" | "signout" | null>(null);
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  const leave = async () => {
    if (busy) return;
    setBusy("signout");
    try {
      await signOut(); // the root shows the sign-in screen and this one unmounts
    } finally {
      if (alive.current) setBusy(null);
    }
  };

  const agree = async () => {
    if (busy) return;
    setBusy("agree");
    // The screen just explained what the location is for: ask for it here, once, instead of the
    // onboarding opening with a system dialog on top of its first question (U1).
    if (Platform.OS !== "web") await Location.requestForegroundPermissionsAsync().catch(() => null);
    startLocation({ ask: false }); // the fix is in by the end of onboarding; not awaited, the screen moves on
    await giveConsent();
  };

  return (
    <View style={[styles.root, { backgroundColor: colors.surface }]}>
      <ScrollView contentContainerStyle={[styles.wrap, { paddingTop: insets.top + spacing.xxxl }]}>
        <Image source={require("../../assets/images/avatar.png")} style={[styles.avatar, { backgroundColor: colors.avatarBg }]} />
        <Text style={[styles.title, { color: colors.onSurface }]}>Before we start</Text>
        <Text style={[styles.sub, { color: colors.onSurfaceSecondary }]}>TruckBuddy uses outside services to hear you and find your way.</Text>
        {ITEMS.map((it) => (
          <View key={it.title} style={[styles.item, { backgroundColor: colors.surfaceSecondary }]}>
            <Icon name={it.icon as never} size={24} color={colors.info} />
            <View style={styles.flex}>
              <Text style={[styles.itemTitle, { color: colors.onSurface }]}>{it.title}</Text>
              <Text style={[styles.itemBody, { color: colors.onSurfaceSecondary }]}>{it.body}</Text>
            </View>
          </View>
        ))}
      </ScrollView>
      <View style={[styles.footer, { paddingBottom: insets.bottom + spacing.lg }]}>
        {/* In the footer, not at the end of the scroll: there it sat half under the button on a phone screen. */}
        <View style={styles.links}>
          <PressableScale testID="consent-privacy-link" accessibilityRole="link" onPress={() => WebBrowser.openBrowserAsync(PRIVACY_URL).catch(() => {})} style={styles.linkHit}>
            <Text style={[styles.link, styles.center, { color: colors.info }]}>Privacy policy</Text>
          </PressableScale>
          <PressableScale testID="consent-terms-link" accessibilityRole="link" onPress={() => WebBrowser.openBrowserAsync(TERMS_URL).catch(() => {})} style={styles.linkHit}>
            <Text style={[styles.link, styles.center, { color: colors.info }]}>Terms of use</Text>
          </PressableScale>
        </View>
        <PressableScale
          testID="consent-agree"
          accessibilityLabel={t("Agree and continue")}
          onPress={() => agree()}
          disabled={!!busy}
          style={[styles.button, { backgroundColor: colors.brandPrimary }]}
        >
          {busy === "agree" ? <ActivityIndicator color={colors.onBrandPrimary} /> : <Text style={[styles.buttonText, { color: colors.onBrandPrimary }]}>Agree and continue</Text>}
        </PressableScale>
        {/* Secondary on purpose: a text action under the button, quieter than the links above, still 56 pt tall. */}
        <PressableScale
          testID="consent-sign-out"
          accessibilityRole="button"
          accessibilityLabel={t("Sign out")}
          accessibilityState={{ disabled: !!busy, busy: busy === "signout" }}
          onPress={leave}
          disabled={!!busy}
          style={[styles.linkHit, styles.signOutHit]}
        >
          {busy === "signout" ? (
            <ActivityIndicator color={colors.onSurfaceSecondary} />
          ) : (
            <Text style={[styles.link, styles.center, { color: colors.onSurfaceSecondary }]}>Sign out</Text>
          )}
        </PressableScale>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1 },
  // Wraps: "Политика конфиденциальности" and "Условия использования" don't fit one row (QA 2026-11-09, bug 5).
  links: { flexDirection: "row", flexWrap: "wrap", justifyContent: "center", columnGap: spacing.xl },
  // Text links still get a 56 pt target (in-cab rule); the label stays a link, the row around it is the hit zone.
  linkHit: { minHeight: 56, minWidth: 56, maxWidth: "100%", justifyContent: "center", paddingHorizontal: spacing.sm },
  signOutHit: { alignSelf: "center", alignItems: "center" },
  wrap: { alignItems: "center", paddingHorizontal: spacing.xl, gap: spacing.md, paddingBottom: spacing.md },
  avatar: { width: 96, height: 96, borderRadius: 48, marginBottom: spacing.sm },
  title: { fontFamily: fonts.bold, fontSize: 26, textAlign: "center" },
  sub: { fontFamily: fonts.regular, fontSize: 16, textAlign: "center", marginBottom: spacing.sm },
  item: { alignSelf: "stretch", flexDirection: "row", gap: spacing.md, padding: spacing.lg, borderRadius: radius.md },
  flex: { flex: 1 },
  itemTitle: { fontFamily: fonts.semibold, fontSize: 16, marginBottom: 2 },
  itemBody: { fontFamily: fonts.regular, fontSize: 14, lineHeight: 20 },
  link: { fontFamily: fonts.semibold, fontSize: 15, paddingVertical: spacing.sm },
  footer: { paddingHorizontal: spacing.xl, paddingTop: spacing.md, gap: spacing.xs },
  center: { textAlign: "center" },
  button: { height: 56, borderRadius: radius.pill, alignItems: "center", justifyContent: "center" },
  buttonText: { fontFamily: fonts.bold, fontSize: 17 },
});
