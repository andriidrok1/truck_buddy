import { StyleSheet, Text, View } from "react-native";
import Animated, { FadeInUp, FadeOutUp } from "react-native-reanimated";

import { chipAhead, type Advisory } from "../geo/advisories";
import { fonts, radius, spacing, useTheme } from "../theme";
import { Icon } from "./Icon";

const ICON: Record<Advisory["kind"], string> = { incident: "alert-octagon", weather: "weather-snowy-heavy", chain: "link-variant" };
const MAX = 3;

/** Up to three chips under the nav banner: what is ahead and how far. */
export function AdvisoryStrip({ advisories }: { advisories: Advisory[] }) {
  const { colors } = useTheme();
  if (!advisories.length) return null;
  return (
    <View testID="advisory-strip" style={styles.strip} pointerEvents="none">
      {advisories.slice(0, MAX).map((a) => {
        const urgent = a.severity === 3;
        return (
          <Animated.View
            key={`${a.kind}:${a.id}`}
            entering={FadeInUp.duration(220)}
            exiting={FadeOutUp.duration(160)}
            testID={`advisory-${a.kind}`}
            style={[styles.chip, { backgroundColor: urgent ? colors.error : colors.surfaceInverse }]}
          >
            <Icon name={ICON[a.kind]} size={16} color={urgent ? colors.onError : colors.warning} />
            <Text numberOfLines={1} style={[styles.text, { color: urgent ? colors.onError : colors.onSurfaceInverse }]}>
              {a.text}
            </Text>
            <Text style={[styles.ahead, { color: urgent ? colors.onError : colors.onSurfaceInverse }]}>{chipAhead(a.aheadM)}</Text>
          </Animated.View>
        );
      })}
    </View>
  );
}

const styles = StyleSheet.create({
  strip: { gap: spacing.xs, marginTop: spacing.sm },
  chip: { flexDirection: "row", alignItems: "center", gap: spacing.sm, alignSelf: "flex-start", maxWidth: "100%", paddingVertical: 6, paddingHorizontal: spacing.md, borderRadius: radius.pill },
  text: { flexShrink: 1, fontFamily: fonts.semibold, fontSize: 13 },
  ahead: { fontFamily: fonts.bold, fontSize: 13, opacity: 0.85 },
});
