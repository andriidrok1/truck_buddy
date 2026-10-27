import * as WebBrowser from "expo-web-browser";
import { useState } from "react";
import { ActivityIndicator, Image, ScrollView, StyleSheet, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { PRIVACY_URL } from "../constants";
import { giveConsent } from "../consent";
import { fonts, radius, spacing, useTheme } from "../theme";
import { Icon } from "./Icon";
import { PressableScale } from "./PressableScale";

const ITEMS: { icon: string; title: string; body: string }[] = [
  {
    icon: "microphone",
    title: "Your voice goes to OpenAI",
    body: "When you talk to TruckBuddy, the recording and its transcript are sent to OpenAI to understand you and to speak the answer back.",
  },
  {
    icon: "map-marker",
    title: "Your location goes to map services",
    body: "Your position and truck size are sent to HERE, OpenRouteService, Google and OpenStreetMap to find stops and plan truck-safe routes.",
  },
  {
    icon: "shield-check",
    title: "Nothing is sold or used for ads",
    body: "These services only process your request. TruckBuddy doesn't track you across apps.",
  },
];

/** Shown once after the access code: the driver agrees to sharing voice and location with third-party services. */
export function ConsentGate() {
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const [busy, setBusy] = useState(false);

  const agree = async () => {
    setBusy(true);
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
        <PressableScale testID="consent-privacy-link" accessibilityRole="link" onPress={() => WebBrowser.openBrowserAsync(PRIVACY_URL).catch(() => {})}>
          <Text style={[styles.link, { color: colors.info }]}>Read the privacy policy</Text>
        </PressableScale>
      </ScrollView>
      <View style={[styles.footer, { paddingBottom: insets.bottom + spacing.lg }]}>
        <PressableScale
          testID="consent-agree"
          accessibilityLabel="Agree and continue"
          onPress={agree}
          disabled={busy}
          style={[styles.button, { backgroundColor: colors.brandPrimary }]}
        >
          {busy ? <ActivityIndicator color={colors.onBrandPrimary} /> : <Text style={[styles.buttonText, { color: colors.onBrandPrimary }]}>Agree and continue</Text>}
        </PressableScale>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1 },
  wrap: { alignItems: "center", paddingHorizontal: spacing.xl, gap: spacing.md, paddingBottom: spacing.xl },
  avatar: { width: 96, height: 96, borderRadius: 48, marginBottom: spacing.sm },
  title: { fontFamily: fonts.bold, fontSize: 26, textAlign: "center" },
  sub: { fontFamily: fonts.regular, fontSize: 16, textAlign: "center", marginBottom: spacing.sm },
  item: { alignSelf: "stretch", flexDirection: "row", gap: spacing.md, padding: spacing.lg, borderRadius: radius.md },
  flex: { flex: 1 },
  itemTitle: { fontFamily: fonts.semibold, fontSize: 16, marginBottom: 2 },
  itemBody: { fontFamily: fonts.regular, fontSize: 14, lineHeight: 20 },
  link: { fontFamily: fonts.semibold, fontSize: 15, paddingVertical: spacing.sm },
  footer: { paddingHorizontal: spacing.xl, paddingTop: spacing.md },
  button: { height: 56, borderRadius: radius.pill, alignItems: "center", justifyContent: "center" },
  buttonText: { fontFamily: fonts.bold, fontSize: 17 },
});
