import { useState } from "react";
import { StyleSheet, View } from "react-native";

import { t } from "../i18n";
import { Text } from "../i18n/Text";
import { frameClock, type RadarFrame } from "../map/mapView";
import { fonts, radius, shadow, spacing, useTheme } from "../theme";
import { Icon } from "./Icon";
import { PressableScale } from "./PressableScale";

/** The round "map view" button in the right column, above Follow truck; blue while the map isn't the plain one. */
export function MapViewButton({ bottom, active, onPress }: { bottom: number; active: boolean; onPress: () => void }) {
  const { colors } = useTheme();
  return (
    <PressableScale
      testID="map-view-button"
      accessibilityLabel={t("Map view")}
      onPress={onPress}
      containerStyle={[styles.side, { bottom }]}
      style={[styles.round, shadow(colors), { backgroundColor: active ? colors.info : colors.surface }]}
    >
      <Icon name="layers" size={26} color={active ? colors.onInfo : colors.onSurface} />
    </PressableScale>
  );
}

/** "Satellite near the stop · Map" (Figma V9): the map turned satellite on its own; one tap keeps the plain map
 *  for this stop. */
export function AutoSatelliteChip({ top, onMap }: { top: number; onMap: () => void }) {
  const { colors } = useTheme();
  return (
    <View pointerEvents="box-none" style={[styles.status, { top }]}>
      <View testID="auto-satellite" style={[styles.chip, shadow(colors), { backgroundColor: colors.surface }]}>
        <Icon name="satellite-variant" size={18} color={colors.info} />
        <Text style={[styles.chipText, { color: colors.onSurface }]}>Satellite near the stop</Text>
        <PressableScale testID="auto-satellite-off" accessibilityRole="button" hitSlop={12} onPress={onMap} style={styles.chipBtn}>
          <Text style={[styles.chipText, { color: colors.info }]}>Map</Text>
        </PressableScale>
      </View>
    </View>
  );
}

const FORECAST_DOT = "#AF52DE";

const KEY_ROWS = [
  { label: "Rain", colors: ["#9BE564", "#3CB043", "#F7D433", "#F28C28", "#E5322D"] },
  { label: "Mix", colors: ["#F6AAD7", "#EC80C4"] },
  { label: "Snow", colors: ["#B7D7FF", "#5B8DEF", "#2F4FD1", "#1E288C"] },
];

/** Under the top bar in the weather view: when the radar frame is from, and a Key button that opens the legend
 *  (Figma V2a/V2b). Nothing when the backend has no frame. */
export function RadarStatus({ top, frame, forecastAt, beyondForecast, offlineSince }: { top: number; frame: RadarFrame | null; forecastAt?: Date | null; beyondForecast?: boolean; offlineSince?: number | null }) {
  const { colors } = useTheme();
  const [open, setOpen] = useState(false);
  const clock = frameClock(frame?.time);
  if (offlineSince !== undefined && offlineSince !== null) {
    // No signal (a mountain pass): the radar is old, the route's forecast was saved before (Figma V8).
    return (
      <View pointerEvents="none" style={[styles.status, { top }]}>
        <View testID="radar-offline" style={[styles.chip, shadow(colors), { backgroundColor: colors.surfaceInverse }]}>
          <Icon name="signal-off" size={18} color={colors.onSurfaceInverse} />
          <Text style={[styles.chipText, { color: colors.onSurfaceInverse }]}>
            {t("No signal · forecast from {time}", { time: frameClock(new Date(offlineSince).toISOString()) ?? "" })}
          </Text>
        </View>
      </View>
    );
  }
  if (!frame?.enabled || !clock) return null;
  return (
    <View pointerEvents="box-none" style={[styles.status, { top }]}>
      <View testID="radar-time" style={[styles.chip, shadow(colors), { backgroundColor: colors.surface }]}>
        {/* A slider moment ahead shows the HRRR model forecast, not radar: said so on the chip (purple dot). */}
        <View style={[styles.dot, { backgroundColor: forecastAt ? FORECAST_DOT : colors.success }]} />
        <Text style={[styles.chipText, { color: colors.onSurface }]}>
          {beyondForecast
            ? t("No radar forecast this far ahead")
            : forecastAt
              ? t("Model forecast · {time}", { time: frameClock(forecastAt.toISOString()) ?? "" })
              : t("Radar · {time}", { time: clock })}
        </Text>
      </View>
      <PressableScale
        testID="radar-key-button"
        accessibilityRole="button"
        accessibilityState={{ expanded: open }}
        onPress={() => setOpen((o) => !o)}
        style={[styles.chip, shadow(colors), { backgroundColor: colors.surface }]}
      >
        <View style={styles.swatch}>
          <View style={[styles.swatchHalf, { backgroundColor: "#3CB043" }]} />
          <View style={[styles.swatchHalf, { backgroundColor: "#5B8DEF" }]} />
        </View>
        <Text style={[styles.chipText, { color: colors.onSurface }]}>Key</Text>
      </PressableScale>
      {open && (
        <View testID="radar-key" style={[styles.key, shadow(colors), { backgroundColor: colors.surface }]}>
          {KEY_ROWS.map((r) => (
            <View key={r.label} style={styles.keyRow}>
              <Text style={[styles.keyLabel, { color: colors.onSurface }]}>{r.label}</Text>
              <View style={styles.bar}>
                {r.colors.map((c) => (
                  <View key={c} style={[styles.barPart, { backgroundColor: c }]} />
                ))}
              </View>
            </View>
          ))}
          <View style={styles.keyEnds}>
            <Text style={[styles.keyEnd, { color: colors.onSurfaceSecondary }]}>light</Text>
            <Text style={[styles.keyEnd, { color: colors.onSurfaceSecondary }]}>heavy</Text>
          </View>
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  side: { position: "absolute", right: spacing.lg },
  round: { width: 56, height: 56, borderRadius: 28, alignItems: "center", justifyContent: "center" },
  status: { position: "absolute", left: spacing.lg, gap: spacing.sm, alignItems: "flex-start" },
  chip: { minHeight: 40, borderRadius: radius.pill, paddingHorizontal: spacing.md, flexDirection: "row", alignItems: "center", gap: spacing.sm },
  chipText: { fontFamily: fonts.bold, fontSize: 14 },
  chipBtn: { paddingHorizontal: spacing.sm, minHeight: 40, justifyContent: "center" },
  dot: { width: 10, height: 10, borderRadius: 5 },
  swatch: { width: 28, height: 8, borderRadius: 4, overflow: "hidden", flexDirection: "row" },
  swatchHalf: { flex: 1 },
  key: { borderRadius: radius.md, padding: spacing.md, gap: spacing.sm, minWidth: 220 },
  keyRow: { flexDirection: "row", alignItems: "center", gap: spacing.md },
  keyLabel: { fontFamily: fonts.bold, fontSize: 14, width: 44 },
  bar: { flex: 1, height: 10, borderRadius: 5, overflow: "hidden", flexDirection: "row" },
  barPart: { flex: 1 },
  keyEnds: { flexDirection: "row", justifyContent: "space-between", paddingLeft: 56 },
  keyEnd: { fontFamily: fonts.semibold, fontSize: 12 },
});
