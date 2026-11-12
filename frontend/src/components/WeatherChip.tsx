import { StyleSheet, View } from "react-native";

import { Text } from "../i18n/Text";
import { fonts, shadow, useTheme } from "../theme";
import { chipValue, KIND_ICON, type Chip } from "../weather/routeWeather";
import { Icon } from "./Icon";

/** Icon colors per kind (Figma "Weather v2"); ice in teal so it never reads as snow. */
export const KIND_COLOR: Record<string, { light: string; dark: string }> = {
  ice: { light: "#00A3BF", dark: "#3CC8DE" },
  snow: { light: "#2F4FD1", dark: "#7FA2FF" },
  wind: { light: "#6E5BD8", dark: "#A99BFF" },
  fog: { light: "#8E8E93", dark: "#AEAEB2" },
  rain: { light: "#2E9E44", dark: "#5BD06F" },
  cloud: { light: "#8E8E93", dark: "#AEAEB2" },
  clear: { light: "#FF9500", dark: "#FFB340" },
};

/** A forecast chip over a point of the route: icon, the main number, when the truck is there. Folded to a dot
 *  when the map is zoomed out and the point isn't one that matters (src/weather/routeWeather.ts chipMode). */
export function WeatherChipView({ chip, mode, clock }: { chip: Chip; mode: "chip" | "dot"; clock: string }) {
  const { colors, scheme } = useTheme();
  const tint = (KIND_COLOR[chip.kind] ?? KIND_COLOR.cloud)[scheme === "dark" ? "dark" : "light"];
  if (mode === "dot") {
    return <View testID={`wx-dot-${chip.index}`} style={[styles.dot, shadow(colors), { backgroundColor: tint, borderColor: colors.surface }]} />;
  }
  return (
    <View testID={`wx-chip-${chip.index}`} style={styles.wrap}>
      <View style={[styles.chip, shadow(colors), { backgroundColor: colors.surface, borderColor: chip.warn ? colors.warning : "transparent" }]}>
        <Icon name={KIND_ICON[chip.kind] ?? "weather-cloudy"} size={24} color={tint} />
        <View>
          <Text style={[styles.value, { color: colors.onSurface }]}>{chipValue(chip)}{chip.kind === "wind" ? " mph" : ""}</Text>
          <Text style={[styles.time, { color: colors.onSurfaceSecondary }]}>{clock}</Text>
        </View>
      </View>
      <View style={[styles.tail, { borderTopColor: chip.warn ? colors.warning : colors.surface }]} />
    </View>
  );
}

/** Chain control on the road (always drawn in the weather view). */
export function ChainMarkerView({ label }: { label: string }) {
  const { colors } = useTheme();
  return (
    <View style={[styles.chain, shadow(colors), { backgroundColor: colors.warning, borderColor: colors.surface }]}>
      <Icon name="link-variant" size={16} color={colors.onWarning} />
      <Text style={[styles.chainText, { color: colors.onWarning }]}>{label}</Text>
    </View>
  );
}

/** Where an NWS alert starts on the route. */
export function AlertMarkerView() {
  const { colors } = useTheme();
  return (
    <View style={[styles.alert, shadow(colors), { backgroundColor: colors.warning, borderColor: colors.surface }]}>
      <Icon name="alert" size={20} color={colors.onWarning} />
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { alignItems: "center" },
  chip: { flexDirection: "row", alignItems: "center", gap: 6, paddingHorizontal: 10, paddingVertical: 6, borderRadius: 14, borderWidth: 2 },
  value: { fontFamily: fonts.extrabold, fontSize: 17 },
  time: { fontFamily: fonts.semibold, fontSize: 13 },
  tail: { width: 0, height: 0, borderLeftWidth: 8, borderRightWidth: 8, borderTopWidth: 9, borderLeftColor: "transparent", borderRightColor: "transparent", marginTop: -1 },
  dot: { width: 16, height: 16, borderRadius: 8, borderWidth: 3 },
  chain: { flexDirection: "row", alignItems: "center", gap: 4, paddingHorizontal: 10, height: 32, borderRadius: 16, borderWidth: 2.5 },
  chainText: { fontFamily: fonts.extrabold, fontSize: 14 },
  alert: { width: 40, height: 40, borderRadius: 20, borderWidth: 3, alignItems: "center", justifyContent: "center" },
});
