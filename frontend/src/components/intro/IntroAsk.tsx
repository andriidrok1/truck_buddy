import { Image, ScrollView, StyleSheet, View } from "react-native";

import { Text } from "../../i18n/Text";
import { fonts, radius, spacing, useTheme } from "../../theme";
import { Icon } from "../Icon";
import { introStyles } from "./introStyles";

// The sound wave next to the mascot: bar heights, mirrored on the other side. Static on purpose (the intro is a
// picture of one request, nothing is being recorded), so not src/components/Waveform.tsx, which animates.
const WAVE = [8, 14, 24, 36, 22, 30, 44];

function Wave({ mirrored }: { mirrored?: boolean }) {
  const { colors } = useTheme();
  return (
    <View style={styles.wave}>
      {(mirrored ? [...WAVE].reverse() : WAVE).map((h, i) => (
        <View key={i} style={[styles.bar, { height: h, backgroundColor: colors.info }]} />
      ))}
    </View>
  );
}

/** Intro, screen 2 (Figma B2): one request top to bottom. What the driver says, the copilot hearing it, the answer. */
export function IntroAsk() {
  const { colors } = useTheme();
  return (
    <ScrollView contentContainerStyle={[introStyles.scroll, introStyles.centered]} showsVerticalScrollIndicator={false}>
      <Text style={[styles.label, { color: colors.onSurfaceSecondary }]}>You say</Text>
      <Text style={[styles.quote, { color: colors.onSurface }]}>“Where can I park tonight?”</Text>

      <View testID="intro-ask-wave" style={styles.hear} accessibilityElementsHidden importantForAccessibility="no-hide-descendants">
        <Wave />
        <View style={[styles.ring, { backgroundColor: colors.avatarGlowSoft, borderColor: colors.avatarGlow }]}>
          <Image source={require("../../../assets/images/avatar.png")} style={[styles.avatar, { backgroundColor: colors.avatarBg }]} />
        </View>
        <Wave mirrored />
      </View>

      <Text style={[styles.label, { color: colors.onSurfaceSecondary }]}>I answer</Text>
      <View accessible style={[styles.card, { backgroundColor: colors.surfaceSecondary }]}>
        <View style={[styles.pin, { backgroundColor: colors.info }]}>
          <Icon name="map-marker" size={24} color={colors.onInfo} />
        </View>
        <View style={styles.flex}>
          <Text style={[styles.place, { color: colors.onSurface }]}>Roady’s Truck Stop</Text>
          {/* Two pieces in a row that wraps: when they do not fit (a large system font), the verdict goes to its own
              line whole instead of breaking in the middle. The green is a dot: as text it is 2:1 on this card. */}
          <View style={styles.meta}>
            <Text style={[styles.metaText, { color: colors.onSurfaceSecondary }]}>4 miles</Text>
            <View style={styles.verdict}>
              <View testID="intro-ask-dot" importantForAccessibility="no" style={[styles.dot, { backgroundColor: colors.success }]} />
              <Text style={[styles.metaText, styles.good, { color: colors.onSurface }]}>parking looks good</Text>
            </View>
          </View>
        </View>
      </View>

      <Text accessibilityRole="header" style={[introStyles.title, introStyles.center, styles.title, { color: colors.onSurface }]}>
        Just ask
      </Text>
      <Text style={[introStyles.text, introStyles.center, { color: colors.onSurfaceSecondary }]}>No typing, no menus. Hands stay on the wheel.</Text>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  label: { fontFamily: fonts.extrabold, fontSize: 13, letterSpacing: 1.5, textTransform: "uppercase", textAlign: "center" },
  quote: { fontFamily: fonts.extrabold, fontSize: 30, lineHeight: 37, letterSpacing: -0.4, textAlign: "center", marginTop: spacing.sm },
  hear: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: spacing.lg, marginVertical: spacing.xl },
  wave: { flexDirection: "row", alignItems: "center", gap: 5, height: 48 },
  bar: { width: 5, borderRadius: 3 },
  ring: { width: 132, height: 132, borderRadius: 66, borderWidth: 3, alignItems: "center", justifyContent: "center" },
  avatar: { width: 108, height: 108, borderRadius: 54 },
  card: { alignSelf: "stretch", flexDirection: "row", alignItems: "center", gap: spacing.md, borderRadius: radius.lg, padding: spacing.lg, marginTop: spacing.md },
  pin: { width: 48, height: 48, borderRadius: 24, alignItems: "center", justifyContent: "center" },
  flex: { flex: 1 },
  place: { fontFamily: fonts.bold, fontSize: 20 },
  meta: { flexDirection: "row", flexWrap: "wrap", alignItems: "center", columnGap: spacing.md },
  metaText: { fontFamily: fonts.semibold, fontSize: 16, lineHeight: 22 },
  verdict: { flexDirection: "row", alignItems: "center", gap: spacing.sm, flexShrink: 1 },
  dot: { width: 10, height: 10, borderRadius: 5 },
  good: { fontFamily: fonts.bold, flexShrink: 1 },
  title: { marginTop: spacing.xl },
});
