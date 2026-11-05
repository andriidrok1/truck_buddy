import { ScrollView, StyleSheet, useWindowDimensions, View } from "react-native";
import { Text } from "../i18n/Text";
import Animated, { FadeInDown, FadeOutDown } from "react-native-reanimated";

import { alternativeLabel } from "../geo/routeChoice";
import { fonts, radius, shadow, spacing, useTheme } from "../theme";
import type { RouteInfo } from "../types";
import { Icon } from "./Icon";
import { PressableScale } from "./PressableScale";

type Props = {
  /** The active route; its `alternatives` are what the driver picks from. */
  primary: RouteInfo;
  /** Distance from the bottom of the screen (above the copilot sheet). */
  bottom: number;
  /** The sheet never grows past this distance from the top of the screen (under the status bar). */
  top: number;
  onPick: (index: number) => void;
  onClose: () => void;
};

function durationLabel(seconds: number) {
  const min = Math.max(1, Math.round(seconds / 60));
  if (min < 60) return `${min} min`;
  const h = Math.floor(min / 60);
  const m = min % 60;
  return m ? `${h} h ${m} min` : `${h} h`;
}

/** "8 min · 4.2 mi · $12.50 tolls" */
export function routeFacts(r: RouteInfo) {
  const parts = [durationLabel(r.duration_s), `${r.distance_mi.toFixed(r.distance_mi < 10 ? 1 : 0)} mi`];
  if (r.tolls_usd) parts.push(`$${r.tolls_usd.toFixed(2)} tolls`);
  return parts.join(" · ");
}

/** The other routes the engine offered for the same destination; tapping one makes it the route. */
export function AlternativesSheet({ primary, bottom, top, onPick, onClose }: Props) {
  const { colors } = useTheme();
  const { height } = useWindowDimensions();
  const alts = primary.alternatives ?? [];
  return (
    <Animated.View
      entering={FadeInDown.duration(240)}
      exiting={FadeOutDown.duration(180)}
      testID="alternatives-sheet"
      // Bounded at the top: at 130 % font the sheet grew upward past the screen and took its title and
      // close button with it; only Back closed it (QA 2026-11-02, bug 33). The rows scroll instead.
      style={[styles.wrap, shadow(colors, 2), { bottom, maxHeight: height - bottom - top, backgroundColor: colors.surface }]}
    >
      <View style={styles.head}>
        <Text style={[styles.title, { color: colors.onSurface }]}>Routes</Text>
        <PressableScale testID="alternatives-close" accessibilityLabel="Close routes" onPress={onClose} style={styles.closeBtn}>
          <Icon name="close" size={22} color={colors.onSurfaceSecondary} />
        </PressableScale>
      </View>
      <ScrollView style={styles.list} contentContainerStyle={styles.listBody}>
        <View testID="alternative-current" style={[styles.row, { backgroundColor: colors.surfaceSecondary }]}>
          <Icon name="check-circle" size={20} color={colors.info} />
          <View style={styles.text}>
            <Text numberOfLines={1} style={[styles.label, { color: colors.onSurface }]}>
              {primary.labels?.length ? primary.labels.join(" / ") : primary.summary}
            </Text>
            <Text style={[styles.facts, { color: colors.onSurfaceSecondary }]}>{routeFacts(primary)} · current</Text>
          </View>
        </View>
        {alts.map((r, i) => (
          <PressableScale
            key={r.handle ?? `${i}-${r.summary}`}
            testID={`alternative-${i}`}
            accessibilityLabel={`Switch to ${alternativeLabel(r, i)}`}
            onPress={() => onPick(i)}
            haptic="medium"
            style={[styles.row, { borderColor: colors.border }]}
          >
            <Icon name="directions-fork" size={20} color={colors.onSurfaceSecondary} />
            <View style={styles.text}>
              <Text numberOfLines={1} style={[styles.label, { color: colors.onSurface }]}>
                {alternativeLabel(r, i)}
              </Text>
              <Text style={[styles.facts, { color: colors.onSurfaceSecondary }]}>{routeFacts(r)}</Text>
            </View>
            <Icon name="chevron-right" size={20} color={colors.onSurfaceSecondary} />
          </PressableScale>
        ))}
      </ScrollView>
    </Animated.View>
  );
}

const styles = StyleSheet.create({
  wrap: { position: "absolute", left: spacing.lg, right: spacing.lg, borderRadius: radius.lg, padding: spacing.md, gap: spacing.sm },
  list: { flexGrow: 0 },
  listBody: { gap: spacing.sm },
  head: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingLeft: spacing.xs },
  title: { fontFamily: fonts.bold, fontSize: 18 },
  closeBtn: { width: 44, height: 44, alignItems: "center", justifyContent: "center" },
  row: { flexDirection: "row", alignItems: "center", gap: spacing.sm, minHeight: 56, paddingHorizontal: spacing.md, borderRadius: radius.md, borderWidth: StyleSheet.hairlineWidth, borderColor: "transparent" },
  text: { flex: 1, minWidth: 0 },
  label: { fontFamily: fonts.semibold, fontSize: 15 },
  facts: { fontFamily: fonts.medium, fontSize: 13, marginTop: 1 },
});
