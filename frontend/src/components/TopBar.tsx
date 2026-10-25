import { ActivityIndicator, ScrollView, StyleSheet, Text, View } from "react-native";

import { CATEGORIES, TRUCK_PROFILE_LABEL, categoryColor } from "../constants";
import { fonts, radius, shadow, spacing, useTheme } from "../theme";
import type { Category } from "../types";
import { Icon } from "./Icon";
import { PressableScale } from "./PressableScale";

type Props = {
  topInset: number;
  selected: Category | null;
  loading: Category | null;
  isDemo: boolean;
  onSelect: (c: Category) => void;
  onLayout: (h: number) => void;
};

export function TopBar({ topInset, selected, loading, isDemo, onSelect, onLayout }: Props) {
  const { colors } = useTheme();
  const card = [styles.white, shadow(colors), { backgroundColor: colors.surface }];

  return (
    <View
      style={[styles.wrap, { paddingTop: topInset + spacing.sm }]}
      pointerEvents="box-none"
      onLayout={(e) => onLayout(e.nativeEvent.layout.height)}
    >
      <View style={styles.row} pointerEvents="box-none">
        <PressableScale testID="menu-button" accessibilityLabel="Menu" style={[card, styles.circle]}>
          <Icon name="menu" size={24} color={colors.onSurface} />
        </PressableScale>

        <View testID="truck-profile-chip" style={[card, styles.profile]}>
          <Icon name="truck" size={20} color={colors.onSurface} />
          <Text style={[styles.profileText, { color: colors.onSurface }]} numberOfLines={1}>
            {TRUCK_PROFILE_LABEL}
          </Text>
        </View>

        <PressableScale testID="layers-button" accessibilityLabel="Map layers" style={[card, styles.circle]}>
          <Icon name="layers-outline" size={24} color={colors.onSurface} />
        </PressableScale>
      </View>

      <ScrollView
        horizontal
        showsHorizontalScrollIndicator={false}
        style={styles.chipRow}
        contentContainerStyle={styles.chipContent}
      >
        {CATEGORIES.map((c) => {
          const active = selected === c.key;
          const tint = categoryColor(colors, c.key);
          return (
            <PressableScale
              key={c.key}
              testID={c.testID}
              accessibilityState={{ selected: active }}
              onPress={() => onSelect(c.key)}
              hitSlop={{ top: 10, bottom: 10 }}
              style={[
                card,
                styles.chip,
                { borderColor: active ? tint : colors.surface },
              ]}
            >
              <View style={[styles.chipIcon, { backgroundColor: tint }]}>
                {loading === c.key ? (
                  <ActivityIndicator size="small" color={colors.onCategory} />
                ) : (
                  <Icon name={c.icon} size={14} color={colors.onCategory} />
                )}
              </View>
              <Text style={[styles.chipText, { color: colors.onSurface }]}>{c.label}</Text>
            </PressableScale>
          );
        })}
      </ScrollView>

      {isDemo && (
        <View testID="demo-location-tag" style={[card, styles.demo]}>
          <View style={[styles.demoDot, { backgroundColor: colors.warning }]} />
          <Text style={[styles.demoText, { color: colors.onSurfaceSecondary }]}>Demo location</Text>
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { position: "absolute", top: 0, left: 0, right: 0 },
  row: { flexDirection: "row", alignItems: "center", paddingHorizontal: spacing.lg, gap: spacing.md },
  white: {},
  circle: { width: 56, height: 56, borderRadius: 28, alignItems: "center", justifyContent: "center" },
  profile: {
    flex: 1,
    height: 48,
    borderRadius: radius.pill,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: spacing.sm,
    paddingHorizontal: spacing.md,
  },
  profileText: { fontFamily: fonts.semibold, fontSize: 15 },
  chipRow: { height: 56, flexGrow: 0, marginTop: spacing.xs },
  chipContent: { paddingHorizontal: spacing.lg, gap: spacing.sm, alignItems: "center" },
  chip: {
    flexShrink: 0,
    height: 36,
    borderRadius: radius.pill,
    flexDirection: "row",
    alignItems: "center",
    paddingLeft: 6,
    paddingRight: 14,
    gap: spacing.sm,
    borderWidth: 1.5,
  },
  chipIcon: { width: 24, height: 24, borderRadius: 12, alignItems: "center", justifyContent: "center" },
  chipText: { fontFamily: fonts.semibold, fontSize: 14 },
  demo: {
    alignSelf: "flex-start",
    marginLeft: spacing.lg,
    height: 28,
    borderRadius: radius.pill,
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: spacing.md,
    gap: 6,
  },
  demoDot: { width: 8, height: 8, borderRadius: 4 },
  demoText: { fontFamily: fonts.medium, fontSize: 12 },
});
