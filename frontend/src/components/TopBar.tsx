import { StyleSheet, Text, View } from "react-native";

import { fonts, radius, shadow, spacing, useTheme } from "../theme";
import { Icon } from "./Icon";
import { PressableScale } from "./PressableScale";

type Props = {
  topInset: number;
  truckLabel: string;
  hazmat: boolean;
  onTruckPress: () => void;
  isDemo: boolean;
  onLayout: (h: number) => void;
};

export function TopBar({ topInset, truckLabel, hazmat, onTruckPress, isDemo, onLayout }: Props) {
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

        <PressableScale testID="truck-profile-chip" accessibilityLabel="Edit truck" onPress={onTruckPress} containerStyle={styles.profileWrap} style={[card, styles.profile]}>
          <Icon name="truck" size={20} color={colors.onSurface} />
          <Text style={[styles.profileText, { color: colors.onSurface }]} numberOfLines={1}>
            {truckLabel}
          </Text>
          {hazmat && (
            <View testID="truck-hazmat-badge" style={[styles.hazmat, { backgroundColor: colors.warning }]}>
              <Icon name="biohazard" size={14} color={colors.onWarning} />
            </View>
          )}
          <Icon name="pencil-outline" size={16} color={colors.muted} />
        </PressableScale>

        <PressableScale testID="layers-button" accessibilityLabel="Map layers" style={[card, styles.circle]}>
          <Icon name="layers-outline" size={24} color={colors.onSurface} />
        </PressableScale>
      </View>

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
  profileWrap: { flex: 1 },
  hazmat: { width: 22, height: 22, borderRadius: 11, alignItems: "center", justifyContent: "center" },
  profile: {
    height: 48,
    borderRadius: radius.pill,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: spacing.sm,
    paddingHorizontal: spacing.md,
  },
  profileText: { fontFamily: fonts.semibold, fontSize: 15 },
  demo: {
    alignSelf: "flex-start",
    marginLeft: spacing.lg,
    marginTop: spacing.sm,
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
