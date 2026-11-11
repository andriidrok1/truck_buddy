import { StyleSheet, View } from "react-native";
import { Text } from "../i18n/Text";

import { alternativeLabel, routeFacts } from "../geo/routeChoice";
import { fonts, radius, spacing, useTheme } from "../theme";
import type { RouteInfo } from "../types";
import { Icon } from "./Icon";
import { PressableScale } from "./PressableScale";
import { t } from "../i18n";

export { routeFacts, routeTag } from "../geo/routeChoice";

type Props = {
  /** The active route; its `alternatives` are what the driver picks from. */
  primary: RouteInfo;
  onPick: (index: number) => void;
  onClose: () => void;
};

/** The other routes the engine offered for the same destination; tapping one makes it the route. It is drawn
 *  inside the copilot sheet in place of the route card (CopilotSheet `routesPanel`), not over the map: as a panel
 *  over the map it covered the very lines the driver was choosing between and cut the third route off (UI review
 *  2026-11-05, N7). The sheet body scrolls when the list is longer than the room. */
export function AlternativesSheet({ primary, onPick, onClose }: Props) {
  const { colors } = useTheme();
  const alts = primary.alternatives ?? [];
  return (
    <View testID="alternatives-sheet" style={styles.wrap}>
      <View style={styles.head}>
        <Text style={[styles.title, { color: colors.onSurface }]}>Routes</Text>
        <PressableScale testID="alternatives-close" accessibilityLabel={t("Close routes")} onPress={onClose} style={[styles.closeBtn, { backgroundColor: colors.surfaceSecondary }]}>
          <Icon name="close" size={24} color={colors.onSurface} />
        </PressableScale>
      </View>
      <View testID="alternative-current" style={[styles.row, { backgroundColor: colors.surfaceSecondary }]}>
        <Icon name="check-circle" size={22} color={colors.info} />
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
          accessibilityLabel={t("Switch to {name}", { name: t(alternativeLabel(r, i)) })}
          onPress={() => onPick(i)}
          haptic="medium"
          style={[styles.row, { borderColor: colors.border }]}
        >
          <Icon name="directions-fork" size={22} color={colors.onSurfaceSecondary} />
          <View style={styles.text}>
            <Text numberOfLines={1} style={[styles.label, { color: colors.onSurface }]}>
              {alternativeLabel(r, i)}
            </Text>
            <Text style={[styles.facts, { color: colors.onSurfaceSecondary }]}>{routeFacts(r)}</Text>
          </View>
          <Icon name="chevron-right" size={22} color={colors.onSurfaceSecondary} />
        </PressableScale>
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { gap: spacing.sm },
  head: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  title: { fontFamily: fonts.bold, fontSize: 22 },
  closeBtn: { width: 56, height: 56, borderRadius: 28, alignItems: "center", justifyContent: "center" },
  row: { flexDirection: "row", alignItems: "center", gap: spacing.sm, minHeight: 64, paddingHorizontal: spacing.md, paddingVertical: spacing.sm, borderRadius: radius.md, borderWidth: StyleSheet.hairlineWidth, borderColor: "transparent" },
  text: { flex: 1, minWidth: 0 },
  label: { fontFamily: fonts.semibold, fontSize: 17 },
  facts: { fontFamily: fonts.medium, fontSize: 15, marginTop: 1 },
});
