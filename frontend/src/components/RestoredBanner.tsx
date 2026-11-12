import { StyleSheet, View } from "react-native";
import { Text } from "../i18n/Text";

import { fonts, radius, shadow, spacing, useTheme } from "../theme";
import { withoutCountry } from "../utils/placeName";
import { Icon } from "./Icon";
import { PressableScale } from "./PressableScale";
import { t } from "../i18n";

type Props = {
  /** Destination of the route that came back from disk. */
  placeName: string;
  /** Second line, e.g. "Map saved" (an offline pack covers this route). */
  note?: string | null;
  top: number;
  onResume: () => void;
  onDismiss: () => void;
  /** The banner's drawn height, so a toast can sit under it instead of over its buttons. */
  onHeight?: (h: number) => void;
};

/** "Route restored: Pilot Sacramento" after a restart: Resume (drive) or Drop route (forget it); left alone,
 *  the banner goes after 20 s and the route stays in preview (the screen's timer). */
export function RestoredBanner({ placeName, note, top, onResume, onDismiss, onHeight }: Props) {
  const { colors } = useTheme();
  return (
    // No entering/exiting layout animation: this view mounts in the app's first frames, and Reanimated
    // dropped the animation on 4 of 7 cold starts, leaving the banner invisible while the state said
    // "restored" (QA 2026-10-31, Н23). A plain view is always there.
    <View style={[styles.wrap, { top }]} pointerEvents="box-none" onLayout={(e) => onHeight?.(e.nativeEvent.layout.height)}>
      <View testID="restored-banner" style={[styles.card, shadow(colors, 2), { backgroundColor: colors.surface }]}>
        {/* Buttons on their own row: beside them the name got a third of the width and read "Reno, NV,
            United S…" (QA 2026-11-02, bug 34). */}
        <View style={styles.row}>
          <Icon name="history" size={22} color={colors.info} />
          <View style={styles.text}>
            <Text testID="restored-title" numberOfLines={2} style={[styles.title, { color: colors.onSurface }]}>
              Route restored: {withoutCountry(placeName)}
            </Text>
            {!!note && (
              <Text testID="restored-note" numberOfLines={1} style={[styles.note, { color: colors.onSurfaceSecondary }]}>
                {note}
              </Text>
            )}
          </View>
        </View>
        <View style={styles.actions}>
          <PressableScale testID="restored-dismiss" accessibilityLabel={t("Drop the restored route")} onPress={onDismiss} style={styles.btn}>
            <Text style={[styles.btnText, { color: colors.error }]}>Drop route</Text>
          </PressableScale>
          <PressableScale testID="restored-resume" accessibilityLabel={t("Resume route")} onPress={onResume} style={[styles.btn, styles.primary, { backgroundColor: colors.info }]}>
            <Text style={[styles.btnText, { color: colors.onInfo }]}>Resume</Text>
          </PressableScale>
        </View>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { position: "absolute", left: spacing.lg, right: spacing.lg },
  card: { gap: spacing.sm, paddingHorizontal: spacing.md, paddingVertical: spacing.sm, borderRadius: radius.md },
  row: { flexDirection: "row", alignItems: "center", gap: spacing.sm },
  actions: { flexDirection: "row", justifyContent: "flex-end", gap: spacing.sm },
  text: { flex: 1, minWidth: 0 },
  title: { fontFamily: fonts.semibold, fontSize: 14 },
  note: { fontFamily: fonts.regular, fontSize: 12 },
  btn: { minHeight: 36, paddingHorizontal: spacing.md, borderRadius: radius.pill, alignItems: "center", justifyContent: "center" },
  primary: {},
  btnText: { fontFamily: fonts.semibold, fontSize: 13 },
});
