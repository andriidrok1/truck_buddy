import { StyleSheet, Text, View } from "react-native";
import Animated, { FadeInUp, FadeOutUp } from "react-native-reanimated";

import { fonts, radius, shadow, spacing, useTheme } from "../theme";
import { Icon } from "./Icon";
import { PressableScale } from "./PressableScale";

type Props = {
  /** Destination of the route that came back from disk. */
  placeName: string;
  /** Second line, e.g. "Map saved" (an offline pack covers this route). */
  note?: string | null;
  top: number;
  onResume: () => void;
  onDismiss: () => void;
};

/** "Route restored: Pilot Sacramento" after a restart, with Resume (drive) and Dismiss (forget it). */
export function RestoredBanner({ placeName, note, top, onResume, onDismiss }: Props) {
  const { colors } = useTheme();
  return (
    <Animated.View entering={FadeInUp.duration(220)} exiting={FadeOutUp.duration(180)} style={[styles.wrap, { top }]} pointerEvents="box-none">
      <View testID="restored-banner" style={[styles.card, shadow(colors, 2), { backgroundColor: colors.surface }]}>
        <Icon name="history" size={22} color={colors.info} />
        <View style={styles.text}>
          <Text testID="restored-title" numberOfLines={1} style={[styles.title, { color: colors.onSurface }]}>
            Route restored: {placeName}
          </Text>
          {!!note && (
            <Text testID="restored-note" numberOfLines={1} style={[styles.note, { color: colors.onSurfaceSecondary }]}>
              {note}
            </Text>
          )}
        </View>
        <PressableScale testID="restored-dismiss" accessibilityLabel="Dismiss restored route" onPress={onDismiss} style={styles.btn}>
          <Text style={[styles.btnText, { color: colors.onSurfaceSecondary }]}>Dismiss</Text>
        </PressableScale>
        <PressableScale testID="restored-resume" accessibilityLabel="Resume route" onPress={onResume} style={[styles.btn, styles.primary, { backgroundColor: colors.info }]}>
          <Text style={[styles.btnText, { color: colors.onInfo }]}>Resume</Text>
        </PressableScale>
      </View>
    </Animated.View>
  );
}

const styles = StyleSheet.create({
  wrap: { position: "absolute", left: spacing.lg, right: spacing.lg },
  card: { flexDirection: "row", alignItems: "center", gap: spacing.sm, paddingHorizontal: spacing.md, paddingVertical: spacing.sm, borderRadius: radius.md },
  text: { flex: 1, minWidth: 0 },
  title: { fontFamily: fonts.semibold, fontSize: 14 },
  note: { fontFamily: fonts.regular, fontSize: 12 },
  btn: { minHeight: 36, paddingHorizontal: spacing.md, borderRadius: radius.pill, alignItems: "center", justifyContent: "center" },
  primary: {},
  btnText: { fontFamily: fonts.semibold, fontSize: 13 },
});
