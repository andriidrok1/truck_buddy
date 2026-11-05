import { StyleSheet, View } from "react-native";
import Animated, { FadeInDown, FadeOutDown } from "react-native-reanimated";

import { Text } from "../i18n/Text";
import { fonts, radius, shadow, spacing, useTheme } from "../theme";
import { Icon } from "./Icon";
import { PressableScale } from "./PressableScale";

/** The two heights almost every van and reefer has: 13'6" (the eastern legal limit) and 14' (the western). */
export const COMMON_HEIGHTS_IN = [162, 168] as const;

const label = (inches: number) => `${Math.floor(inches / 12)}' ${inches % 12}"`;

type Props = { bottom: number; current: number; onPick: (heightIn: number) => void; onOther: () => void; onHeight?: (h: number) => void };

/** Asks the truck height until the driver answers (src/truck/heightConfirmed.ts). No "Not now": routes under
 *  bridges go by this number. One tap for the common heights, the truck screen for anything else. The three
 *  answers weigh the same: a filled 13'6" read as "already chosen" (UI review 2026-11-05), and nothing is chosen
 *  until the driver taps. `current` is only voiced to screen readers. */
export function HeightConfirmCard({ bottom, current, onPick, onOther, onHeight }: Props) {
  const { colors } = useTheme();
  return (
    <Animated.View
      entering={FadeInDown.duration(240)}
      exiting={FadeOutDown.duration(180)}
      // Its height lifts the Follow truck button above it (UI review 2026-11-05, M6).
      onLayout={(e) => onHeight?.(e.nativeEvent.layout.height)}
      style={[styles.wrap, shadow(colors, 2), { bottom, backgroundColor: colors.surface }]}
      testID="height-confirm-card"
    >
      <View style={styles.row}>
        <View style={[styles.iconWrap, { backgroundColor: colors.surfaceSecondary }]}>
          <Icon name="truck" size={24} color={colors.info} />
        </View>
        <Text testID="height-confirm-text" style={[styles.text, { color: colors.onSurface }]}>
          How tall is your truck? I plan routes under bridges by it.
        </Text>
      </View>
      <View style={styles.row}>
        {COMMON_HEIGHTS_IN.map((h) => (
          <PressableScale
            key={h}
            testID={`height-confirm-${h}`}
            accessibilityLabel={`My truck is ${Math.floor(h / 12)} feet ${h % 12} inches`}
            accessibilityHint={h === current ? "Saved in your truck profile now" : undefined}
            onPress={() => onPick(h)}
            containerStyle={styles.flex}
            style={[styles.btn, { backgroundColor: colors.surfaceSecondary, borderColor: colors.borderStrong }]}
          >
            <Text style={[styles.btnText, { color: colors.onSurface }]}>{label(h)}</Text>
          </PressableScale>
        ))}
        <PressableScale
          testID="height-confirm-other"
          accessibilityLabel="Another height"
          onPress={onOther}
          containerStyle={styles.flex}
          style={[styles.btn, { backgroundColor: colors.surfaceSecondary, borderColor: colors.borderStrong }]}
        >
          <Text style={[styles.btnText, { color: colors.onSurface }]}>Other</Text>
        </PressableScale>
      </View>
    </Animated.View>
  );
}

const styles = StyleSheet.create({
  wrap: { position: "absolute", left: spacing.lg, right: spacing.lg, borderRadius: radius.lg, padding: spacing.lg, gap: spacing.md },
  row: { flexDirection: "row", alignItems: "center", gap: spacing.sm },
  iconWrap: { width: 44, height: 44, borderRadius: 22, alignItems: "center", justifyContent: "center" },
  text: { flex: 1, fontFamily: fonts.semibold, fontSize: 17, lineHeight: 23 },
  flex: { flex: 1 },
  btn: { height: 56, borderRadius: radius.md, borderWidth: 1, alignItems: "center", justifyContent: "center" },
  btnText: { fontFamily: fonts.bold, fontSize: 17 },
});
