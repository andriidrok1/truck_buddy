import { Modal, Pressable, ScrollView, StyleSheet, useWindowDimensions, View } from "react-native";
import { SafeAreaView, useSafeAreaInsets } from "react-native-safe-area-context";

import { t } from "../i18n";
import { Text } from "../i18n/Text";
import { fonts, radius, shadow, spacing, useTheme } from "../theme";
import { Avatar } from "./Avatar";
import { Icon } from "./Icon";
import { PressableScale } from "./PressableScale";

/** What a driver can say, as chips. A tap asks it in the driver's language (t()); `motive` marks the one that needs
 *  hours of service from Motive (the badge goes once a Motive key is connected). */
export const HOW_TO_CHIPS: { text: string; motive?: boolean }[] = [
  { text: "Find me diesel" },
  { text: "Find a CAT scale" },
  { text: "Take me to Reno" },
  { text: "Weather in Reno" },
  { text: "Where can I park near Stockton?" },
  { text: "How many hours do I have?", motive: true },
];
/** The first-launch sheet sits over the map and keeps the four short ones (Figma 73:2); the full screen has all. */
const FIRST_CHIPS = 4;
/** The map's top row (search, "?", truck) stays visible above the first-launch sheet. */
const SHEET_TOP_GAP = 96;

const STEPS: { icon: string; label: string }[] = [
  { icon: "gesture-tap", label: "Tap me" },
  { icon: "microphone", label: "Say it your way" },
  { icon: "volume-high", label: "I answer out loud" },
];

export type HowToMode = "first" | "help";

type Props = {
  /** null hides it. "first": a sheet over the dimmed map, once after onboarding (Figma 73:2). "help": the whole
   *  screen, from "?" in the top bar or "How to talk to me" in the sheet (Figma 69:2). */
  mode: HowToMode | null;
  /** The driver's name for "All set, {name}." (first launch only). */
  name?: string | null;
  /** A Motive key is connected: the "Needs Motive" badge is hidden. */
  motive: boolean;
  /** A chip: the phrase in the driver's language. The caller closes the screen and sends it as a typed request. */
  onAsk: (phrase: string) => void;
  /** "Try it now": the caller closes the screen and starts listening, as a tap on the avatar does. */
  onTryNow: () => void;
  /** ×, "Not now", Back, a tap on the dimmed map. */
  onClose: () => void;
};

/** "How to use": tap the copilot and talk, with phrases to try (Figma file f5Uha6gopQHRmcGlMXdCD9, section 68:2). */
export function HowToSheet({ mode, name, motive, onAsk, onTryNow, onClose }: Props) {
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const { height: windowH } = useWindowDimensions();
  if (!mode) return null;
  const first = mode === "first";
  const who = name?.trim();
  const chips = first ? HOW_TO_CHIPS.slice(0, FIRST_CHIPS) : HOW_TO_CHIPS;

  const body = (
    <>
      <View style={styles.avatar}>
        <Avatar size={first ? 76 : 112} />
      </View>
      {first && (
        <Text testID="how-to-all-set" style={[styles.allSet, { color: colors.muted }]}>
          {who ? t("All set, {name}. One thing before we drive.", { name: who }) : t("All set. One thing before we drive.")}
        </Text>
      )}
      <Text testID="how-to-title" accessibilityRole="header" style={[styles.title, { color: colors.onSurface }]}>
        Just tap and ask
      </Text>
      <Text style={[styles.subtitle, { color: colors.onSurfaceSecondary }]}>No typing. Talk to me like you&apos;d talk to a friend.</Text>

      <View style={styles.steps}>
        {STEPS.map((s, i) => (
          <View key={s.label} style={styles.stepWrap}>
            {i > 0 && <Icon name="chevron-right" size={20} color={colors.onSurfaceTertiary} style={styles.chevron} />}
            <View style={styles.step}>
              <View style={[styles.stepIcon, { backgroundColor: colors.avatarGlowSoft }]}>
                <Icon name={s.icon} size={26} color={colors.info} />
                <View style={[styles.stepNum, { backgroundColor: colors.brandPrimary, borderColor: colors.surface }]}>
                  <Text style={[styles.stepNumText, { color: colors.onBrandPrimary }]}>{String(i + 1)}</Text>
                </View>
              </View>
              <Text style={[styles.stepLabel, { color: colors.onSurface }]}>{s.label}</Text>
            </View>
          </View>
        ))}
      </View>

      <View style={styles.tryHead}>
        <Text style={[styles.tryTitle, { color: colors.onSurface }]}>Try saying</Text>
        <Text style={[styles.tryHint, { color: colors.muted }]}>Tap one and I ask it</Text>
      </View>
      <View style={styles.chips}>
        {chips.map((c, i) => (
          <PressableScale
            key={c.text}
            testID={`how-to-chip-${i}`}
            accessibilityRole="button"
            accessibilityLabel={c.motive && !motive ? t("{phrase} (needs Motive)", { phrase: t(c.text) }) : t(c.text)}
            onPress={() => onAsk(t(c.text))}
            style={[styles.chip, { backgroundColor: colors.surfaceSecondary }]}
          >
            <Text style={[styles.chipText, { color: colors.onSurface }]}>{c.text}</Text>
            {c.motive && !motive && (
              <View testID="how-to-motive-badge" style={[styles.badge, { borderColor: colors.borderStrong, backgroundColor: colors.surface }]}>
                <Text style={[styles.badgeText, { color: colors.onSurfaceSecondary }]}>Needs Motive</Text>
              </View>
            )}
          </PressableScale>
        ))}
      </View>
    </>
  );

  const actions = (
    <View style={styles.actions}>
      <PressableScale testID="how-to-try" accessibilityRole="button" haptic="medium" onPress={onTryNow} style={[styles.tryBtn, { backgroundColor: colors.brandPrimary }]}>
        <Icon name="microphone" size={22} color={colors.onBrandPrimary} />
        <Text style={[styles.tryBtnText, { color: colors.onBrandPrimary }]}>Try it now</Text>
      </PressableScale>
      {first && (
        <PressableScale testID="how-to-not-now" accessibilityRole="button" onPress={onClose} style={styles.notNow}>
          <Text style={[styles.notNowText, { color: colors.onSurface }]}>Not now</Text>
        </PressableScale>
      )}
    </View>
  );

  return (
    <Modal visible transparent animationType={first ? "slide" : "fade"} onRequestClose={onClose} statusBarTranslucent>
      {first ? (
        <>
          <Pressable testID="how-to-backdrop" accessibilityLabel={t("Not now")} onPress={onClose} style={[styles.backdrop, { backgroundColor: colors.scrim }]} />
          {/* SafeAreaView measures the modal's own window: inside a Modal the provider's insets came back 0 on Android,
              and the bottom buttons sat on the gesture bar. */}
          <SafeAreaView
            edges={["bottom"]}
            testID="how-to"
            style={[styles.sheet, shadow(colors, 2), { backgroundColor: colors.surface, maxHeight: windowH - insets.top - SHEET_TOP_GAP, paddingBottom: spacing.sm }]}
          >
            <View style={[styles.handle, { backgroundColor: colors.surfaceTertiary }]} />
            <ScrollView style={styles.sheetScroll} contentContainerStyle={styles.content} showsVerticalScrollIndicator={false}>
              {body}
            </ScrollView>
            {actions}
          </SafeAreaView>
        </>
      ) : (
        <SafeAreaView edges={["top", "bottom"]} testID="how-to" style={[styles.full, { backgroundColor: colors.surface, paddingBottom: spacing.sm }]}>
          <View style={styles.closeRow}>
            <PressableScale testID="how-to-close" accessibilityRole="button" accessibilityLabel={t("Close")} onPress={onClose} style={[styles.close, { backgroundColor: colors.surfaceSecondary }]}>
              <Icon name="close" size={24} color={colors.onSurface} />
            </PressableScale>
          </View>
          <ScrollView contentContainerStyle={styles.content} showsVerticalScrollIndicator={false}>
            {body}
          </ScrollView>
          {actions}
        </SafeAreaView>
      )}
    </Modal>
  );
}

const styles = StyleSheet.create({
  backdrop: { position: "absolute", top: 0, left: 0, right: 0, bottom: 0 },
  // As tall as its content, never over the search bar's row: with a large font the content scrolls and the
  // buttons stay in view (Figma 73:2).
  sheet: { position: "absolute", left: 0, right: 0, bottom: 0, borderTopLeftRadius: 28, borderTopRightRadius: 28, paddingTop: spacing.sm },
  sheetScroll: { flexGrow: 0, flexShrink: 1 },
  handle: { alignSelf: "center", width: 40, height: 5, borderRadius: 3, marginBottom: spacing.sm },
  full: { flex: 1 },
  closeRow: { flexDirection: "row", justifyContent: "flex-end", paddingHorizontal: spacing.lg, paddingTop: spacing.sm },
  close: { width: 56, height: 56, borderRadius: 28, alignItems: "center", justifyContent: "center" },
  content: { paddingHorizontal: spacing.xl, paddingBottom: spacing.lg, alignItems: "stretch" },
  avatar: { alignItems: "center" },
  allSet: { fontFamily: fonts.medium, fontSize: 15, textAlign: "center", marginTop: spacing.sm },
  title: { fontFamily: fonts.extrabold, fontSize: 28, textAlign: "center", marginTop: spacing.sm },
  subtitle: { fontFamily: fonts.regular, fontSize: 17, lineHeight: 24, textAlign: "center", marginTop: spacing.xs, paddingHorizontal: spacing.lg },
  steps: { flexDirection: "row", justifyContent: "center", marginTop: spacing.xl },
  stepWrap: { flex: 1, flexDirection: "row" },
  chevron: { marginTop: 18, marginHorizontal: -10 },
  step: { flex: 1, alignItems: "center", gap: spacing.sm },
  stepIcon: { width: 56, height: 56, borderRadius: 28, alignItems: "center", justifyContent: "center" },
  stepNum: { position: "absolute", top: -6, right: -8, minWidth: 24, height: 24, borderRadius: 12, borderWidth: 2, alignItems: "center", justifyContent: "center" },
  stepNumText: { fontFamily: fonts.bold, fontSize: 12 },
  stepLabel: { fontFamily: fonts.bold, fontSize: 16, lineHeight: 21, textAlign: "center" },
  tryHead: { flexDirection: "row", alignItems: "baseline", justifyContent: "space-between", flexWrap: "wrap", gap: spacing.sm, marginTop: spacing.xl },
  tryTitle: { fontFamily: fonts.bold, fontSize: 20 },
  tryHint: { fontFamily: fonts.medium, fontSize: 13, flexShrink: 1 },
  chips: { flexDirection: "row", flexWrap: "wrap", gap: spacing.sm, marginTop: spacing.md },
  chip: { minHeight: 56, borderRadius: radius.pill, paddingHorizontal: spacing.lg, paddingVertical: spacing.sm, flexDirection: "row", alignItems: "center", gap: spacing.sm, maxWidth: "100%" },
  chipText: { fontFamily: fonts.semibold, fontSize: 16, flexShrink: 1 },
  badge: { borderWidth: 1, borderRadius: radius.pill, paddingHorizontal: spacing.sm, paddingVertical: 2 },
  badgeText: { fontFamily: fonts.semibold, fontSize: 12 },
  actions: { paddingHorizontal: spacing.xl, paddingTop: spacing.md, gap: spacing.xs },
  tryBtn: { height: 64, borderRadius: radius.pill, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: spacing.sm },
  tryBtnText: { fontFamily: fonts.bold, fontSize: 20 },
  notNow: { height: 56, alignItems: "center", justifyContent: "center" },
  notNowText: { fontFamily: fonts.semibold, fontSize: 17 },
});
