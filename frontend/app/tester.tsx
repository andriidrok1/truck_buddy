import { ScrollView, StyleSheet, Text, View } from "react-native";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { Icon } from "@/src/components/Icon";
import { PressableScale } from "@/src/components/PressableScale";
import { SCENARIOS, setSim, useSim } from "@/src/sim";
import { fonts, radius, shadow, spacing, useTheme } from "@/src/theme";

/** Tester screen (code test123): jump the truck to made-up spots to try scenarios. */
export default function TesterScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { colors } = useTheme();
  const sim = useSim();

  return (
    <View style={[styles.root, { backgroundColor: colors.surfaceSecondary, paddingTop: insets.top }]}>
      <View style={styles.header}>
        <PressableScale testID="tester-back" accessibilityLabel="Back" onPress={() => router.back()} style={[styles.circle, { backgroundColor: colors.surface }]}>
          <Icon name="arrow-left" size={24} color={colors.onSurface} />
        </PressableScale>
        <Text style={[styles.title, { color: colors.onSurface }]}>Test scenarios</Text>
        <View style={styles.circle} />
      </View>

      <ScrollView contentContainerStyle={[styles.body, { paddingBottom: insets.bottom + spacing.xl }]}>
        <View style={[styles.status, { backgroundColor: colors.surface }]}>
          <Icon name={sim ? "map-marker-radius" : "crosshairs-gps"} size={22} color={sim ? colors.warning : colors.success} />
          <Text testID="tester-status" style={[styles.statusText, { color: colors.onSurface }]} numberOfLines={2}>
            {sim ? sim.label : "Using your real location"}
          </Text>
        </View>

        {SCENARIOS.map((s) => {
          const on = sim?.id === s.id;
          return (
            <PressableScale
              key={s.id}
              testID={`scenario-${s.id}`}
              accessibilityLabel={s.title}
              onPress={() => {
                setSim(s);
                router.back();
              }}
              style={[styles.card, shadow(colors), { backgroundColor: colors.surface, borderColor: on ? colors.brandPrimary : colors.surface }]}
            >
              <View style={[styles.icon, { backgroundColor: colors.surfaceSecondary }]}>
                <Icon name={s.icon} size={28} color={colors.onSurface} />
              </View>
              <View style={styles.cardText}>
                <Text style={[styles.cardTitle, { color: colors.onSurface }]}>{s.title}</Text>
                <Text style={[styles.cardSub, { color: colors.onSurfaceSecondary }]}>{s.hint}</Text>
              </View>
              <Icon name={on ? "check-circle" : "chevron-right"} size={24} color={on ? colors.brandPrimary : colors.muted} />
            </PressableScale>
          );
        })}

        {sim && (
          <PressableScale testID="tester-reset" accessibilityLabel="Back to real GPS" onPress={() => setSim(null)} style={[styles.reset, { borderColor: colors.onSurface }]}>
            <Icon name="crosshairs-gps" size={20} color={colors.onSurface} />
            <Text style={[styles.resetText, { color: colors.onSurface }]}>Back to real GPS</Text>
          </PressableScale>
        )}
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1 },
  header: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingHorizontal: spacing.lg, paddingVertical: spacing.sm },
  circle: { width: 48, height: 48, borderRadius: 24, alignItems: "center", justifyContent: "center" },
  title: { fontFamily: fonts.bold, fontSize: 18 },
  body: { padding: spacing.lg, gap: spacing.md },
  status: { flexDirection: "row", alignItems: "center", gap: spacing.md, borderRadius: radius.lg, padding: spacing.lg },
  statusText: { flex: 1, fontFamily: fonts.semibold, fontSize: 15 },
  card: { flexDirection: "row", alignItems: "center", gap: spacing.md, borderRadius: radius.lg, padding: spacing.lg, borderWidth: 2 },
  icon: { width: 52, height: 52, borderRadius: 26, alignItems: "center", justifyContent: "center" },
  cardText: { flex: 1, gap: 2 },
  cardTitle: { fontFamily: fonts.bold, fontSize: 17 },
  cardSub: { fontFamily: fonts.regular, fontSize: 14 },
  reset: { height: 52, borderRadius: radius.pill, borderWidth: 1.5, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: spacing.sm, marginTop: spacing.sm },
  resetText: { fontFamily: fonts.semibold, fontSize: 16 },
});
