// The driver's route preferences as one form: toll roads, ferries and tunnels, favourite truck stop chains,
// break reminder. Shared by the onboarding step and the truck profile screen, so a choice made on day one
// can be changed later (QA 2026-10-28, U10).
import { StyleSheet, View } from "react-native";
import { Text } from "../i18n/Text";

import type { TruckProfile } from "../hooks/useTruckProfile";
import { fonts, radius, spacing, useTheme } from "../theme";
import { PressableScale } from "./PressableScale";
import { ThemedSwitch } from "./ThemedSwitch";
import { t } from "../i18n";

export const CHAINS = ["Pilot", "Flying J", "Love's", "TA", "Petro", "Sapp Bros", "Kwik Trip", "Buc-ee's"];
export const BREAKS: (number | null)[] = [null, 2, 3, 4, 6, 8];

export type Prefs = Pick<TruckProfile, "avoid_tolls" | "avoid_ferries_tunnels" | "favorite_chains" | "break_hours">;

/** A whole-row toggle: the row (56 pt or taller) flips the switch, not only the switch itself. `subtitle` adds a
 *  second line; `onColor` tints the switch when on. Hazmat and Speed limit alerts were bare 48 pt switches on
 *  rows that did nothing when tapped (UI review 2026-11-05, third pass, M4). */
export function ToggleRow({
  title,
  subtitle,
  value,
  onChange,
  onColor,
  testID,
}: {
  title: string;
  subtitle?: string;
  value: boolean;
  onChange: (v: boolean) => void;
  onColor?: string;
  testID: string;
}) {
  const { colors } = useTheme();
  return (
    <PressableScale
      testID={`${testID}-row`}
      accessibilityRole="switch"
      accessibilityState={{ checked: value }}
      accessibilityLabel={t(title)}
      onPress={() => onChange(!value)}
      style={[styles.toggle, !!subtitle && styles.toggleTall, { backgroundColor: colors.surfaceSecondary }]}
    >
      <View style={styles.toggleText}>
        <Text style={[styles.toggleTitle, { color: colors.onSurface }]}>{title}</Text>
        {!!subtitle && <Text style={[styles.toggleSub, { color: colors.onSurfaceSecondary }]}>{subtitle}</Text>}
      </View>
      <ThemedSwitch testID={testID} value={value} onValueChange={onChange} onColor={onColor} />
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
      <View style={styles.chips}>
        {CHAINS.map((c) => (
          <Chip
            key={c}
            label={c}
            on={chains.includes(c)}
            testID={`pref-chain-${c.replace(/[^a-z]/gi, "").toLowerCase()}`}
            onPress={() => onChange({ favorite_chains: chains.includes(c) ? chains.filter((x) => x !== c) : [...chains, c] })}
          />
        ))}
      </View>
      <Text style={[styles.section, { color: colors.onSurface }]}>Break reminder</Text>
      <View style={styles.chips}>
        {BREAKS.map((b) => (
          <Chip key={String(b)} label={b ? `Every ${b} h` : "Off"} on={(value.break_hours ?? null) === b} testID={`pref-break-${b ?? "off"}`} onPress={() => onChange({ break_hours: b })} />
        ))}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  body: { gap: spacing.md },
  toggle: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: spacing.md, borderRadius: radius.md, paddingHorizontal: spacing.lg, minHeight: 56 },
  toggleTall: { paddingVertical: spacing.lg },
  toggleText: { flexShrink: 1 },
  toggleTitle: { fontFamily: fonts.semibold, fontSize: 16, flexShrink: 1 },
  toggleSub: { fontFamily: fonts.regular, fontSize: 13, marginTop: 2 },
  section: { fontFamily: fonts.bold, fontSize: 15, marginTop: spacing.xs },
  // Wrapped rows, not a sideways scroll: a chip cut at the edge ("Petro", "Every 8 h") hid that there was more.
  chips: { flexDirection: "row", flexWrap: "wrap", gap: spacing.sm },
  chip: { flexShrink: 0, height: 56, borderRadius: radius.pill, borderWidth: 1.5, paddingHorizontal: spacing.lg, justifyContent: "center" },
  chipText: { fontFamily: fonts.semibold, fontSize: 16 },
});
