// The driver's route preferences as one form: toll roads, ferries and tunnels, favourite truck stop chains,
// break reminder. Shared by the onboarding step and the truck profile screen, so a choice made on day one
// can be changed later (QA 2026-10-28, U10).
import { ScrollView, StyleSheet, Switch, Text, View } from "react-native";

import type { TruckProfile } from "../hooks/useTruckProfile";
import { fonts, radius, spacing, useTheme } from "../theme";
import { PressableScale } from "./PressableScale";

export const CHAINS = ["Pilot", "Flying J", "Love's", "TA", "Petro", "Sapp Bros", "Kwik Trip", "Buc-ee's"];
export const BREAKS: (number | null)[] = [null, 2, 3, 4, 6, 8];

export type Prefs = Pick<TruckProfile, "avoid_tolls" | "avoid_ferries_tunnels" | "favorite_chains" | "break_hours">;

export function ToggleRow({ title, value, onChange, testID }: { title: string; value: boolean; onChange: (v: boolean) => void; testID: string }) {
  const { colors } = useTheme();
  return (
    <PressableScale testID={`${testID}-row`} onPress={() => onChange(!value)} style={[styles.toggle, { backgroundColor: colors.surfaceSecondary }]}>
      <Text style={[styles.toggleTitle, { color: colors.onSurface }]}>{title}</Text>
      <Switch testID={testID} value={value} onValueChange={onChange} trackColor={{ true: colors.info, false: colors.surfaceTertiary }} />
    </PressableScale>
  );
}

export function Chip({ label, on, onPress, testID }: { label: string; on: boolean; onPress: () => void; testID: string }) {
  const { colors } = useTheme();
  return (
    <PressableScale
      testID={testID}
      onPress={onPress}
      accessibilityState={{ selected: on }}
      aria-selected={on}
      style={[styles.chip, { backgroundColor: on ? colors.info : colors.surface, borderColor: on ? colors.info : colors.border }]}
    >
      <Text style={[styles.chipText, { color: on ? colors.onInfo : colors.onSurface }]}>{label}</Text>
    </PressableScale>
  );
}

/** Toggles and chips bound to `value`; every change comes back as a partial profile. */
export function PrefsForm({ value, onChange }: { value: Prefs; onChange: (patch: Partial<Prefs>) => void }) {
  const { colors } = useTheme();
  const chains = value.favorite_chains ?? [];
  return (
    <View style={styles.body} testID="prefs-form">
      <ToggleRow title="Avoid toll roads" value={!!value.avoid_tolls} onChange={(v) => onChange({ avoid_tolls: v })} testID="pref-avoid-tolls-switch" />
      <ToggleRow title="Avoid ferries and tunnels" value={!!value.avoid_ferries_tunnels} onChange={(v) => onChange({ avoid_ferries_tunnels: v })} testID="pref-avoid-ferries-switch" />
      <Text style={[styles.section, { color: colors.onSurface }]}>Favorite truck stops</Text>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.chips}>
        {CHAINS.map((c) => (
          <Chip
            key={c}
            label={c}
            on={chains.includes(c)}
            testID={`pref-chain-${c.replace(/[^a-z]/gi, "").toLowerCase()}`}
            onPress={() => onChange({ favorite_chains: chains.includes(c) ? chains.filter((x) => x !== c) : [...chains, c] })}
          />
        ))}
      </ScrollView>
      <Text style={[styles.section, { color: colors.onSurface }]}>Break reminder</Text>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.chips}>
        {BREAKS.map((b) => (
          <Chip key={String(b)} label={b ? `Every ${b} h` : "Off"} on={(value.break_hours ?? null) === b} testID={`pref-break-${b ?? "off"}`} onPress={() => onChange({ break_hours: b })} />
        ))}
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  body: { gap: spacing.md },
  toggle: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: spacing.md, borderRadius: radius.md, paddingHorizontal: spacing.lg, minHeight: 56 },
  toggleTitle: { fontFamily: fonts.semibold, fontSize: 16, flexShrink: 1 },
  section: { fontFamily: fonts.bold, fontSize: 15, marginTop: spacing.xs },
  chips: { gap: spacing.sm, paddingRight: spacing.lg },
  chip: { flexShrink: 0, height: 44, borderRadius: radius.pill, borderWidth: 1.5, paddingHorizontal: spacing.lg, justifyContent: "center" },
  chipText: { fontFamily: fonts.semibold, fontSize: 14 },
});
