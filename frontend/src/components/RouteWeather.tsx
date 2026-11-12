import { StyleSheet, View } from "react-native";

import { Text } from "../i18n/Text";
import { frameClock } from "../map/mapView";
import { useRouteWeatherStore } from "../state/routeWeatherStore";
import { fonts, spacing, useTheme } from "../theme";
import type { RouteInfo } from "../types";
import { cardLine, chips, chipValue, detail, KIND_ICON } from "../weather/routeWeather";
import { Icon } from "./Icon";
import { KIND_COLOR } from "./WeatherChip";

const clock = (iso: string) => frameClock(iso) ?? "";
/** The warning orange darkened for text on white (#FF9500 text on white fails contrast). */
const WARN_TEXT_LIGHT = "#B85600";

/** One line in the route card about the first point that matters (Figma V2a): nothing when the way is clear. */
export function RouteWeatherLine({ route }: { route: RouteInfo }) {
  const { colors, scheme } = useTheme();
  const points = useRouteWeatherStore((s) => s.points);
  const line = cardLine(chips(points, route), clock);
  if (!line) return null;
  const tint = (KIND_COLOR[line.kind] ?? KIND_COLOR.cloud)[scheme === "dark" ? "dark" : "light"];
  return (
    <View testID="route-weather-line" style={styles.line}>
      <Icon name={KIND_ICON[line.kind] ?? "weather-cloudy"} size={22} color={tint} />
      <View style={styles.lineText}>
        <Text numberOfLines={2} style={[styles.lineTitle, { color: colors.onSurface }]}>{line.title}</Text>
        <Text numberOfLines={1} style={[styles.lineSub, { color: colors.onSurfaceSecondary }]}>{line.sub}</Text>
      </View>
    </View>
  );
}

/** "Weather on the way" under More (Figma V5): each point at the time the truck gets there. */
export function WeatherOnTheWay({ route }: { route: RouteInfo }) {
  const { colors, scheme } = useTheme();
  const points = useRouteWeatherStore((s) => s.points);
  const list = chips(points, route);
  if (!list.length) return null;
  return (
    <View testID="weather-on-the-way" style={styles.list}>
      <Text style={[styles.head, { color: colors.onSurface }]}>Weather on the way</Text>
      {list.map((c) => {
        const tint = (KIND_COLOR[c.kind] ?? KIND_COLOR.cloud)[scheme === "dark" ? "dark" : "light"];
        return (
          <View key={c.index} testID={`wx-row-${c.index}`} style={styles.row}>
            <Text style={[styles.time, { color: colors.onSurfaceSecondary }]}>{clock(c.eta)}</Text>
            <View style={[styles.dot, { backgroundColor: c.warn ? colors.warning : colors.info, borderColor: colors.surface }]} />
            <View style={styles.rowText}>
              <Text numberOfLines={1} style={[styles.place, { color: colors.onSurface }]}>{c.place?.split(",")[0] ?? "—"}</Text>
              <Text numberOfLines={1} style={[styles.cond, { color: c.warn ? (scheme === "dark" ? colors.warning : WARN_TEXT_LIGHT) : colors.onSurfaceSecondary }]}>{detail(c)}</Text>
            </View>
            <Icon name={KIND_ICON[c.kind] ?? "weather-cloudy"} size={26} color={tint} />
            <Text style={[styles.value, { color: colors.onSurface }]}>{chipValue(c)}</Text>
          </View>
        );
      })}
      <Text style={[styles.source, { color: colors.muted }]}>NWS forecast for when you get there</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  line: { flexDirection: "row", alignItems: "flex-start", gap: spacing.sm, marginTop: spacing.sm },
  lineText: { flex: 1 },
  lineTitle: { fontFamily: fonts.bold, fontSize: 16 },
  lineSub: { fontFamily: fonts.medium, fontSize: 14 },
  list: { marginTop: spacing.md, gap: spacing.sm },
  head: { fontFamily: fonts.extrabold, fontSize: 18 },
  row: { flexDirection: "row", alignItems: "center", gap: spacing.sm, minHeight: 48 },
  time: { fontFamily: fonts.bold, fontSize: 14, width: 64 },
  dot: { width: 14, height: 14, borderRadius: 7, borderWidth: 2 },
  rowText: { flex: 1 },
  place: { fontFamily: fonts.bold, fontSize: 16 },
  cond: { fontFamily: fonts.semibold, fontSize: 14 },
  value: { fontFamily: fonts.extrabold, fontSize: 18, minWidth: 44, textAlign: "right" },
  source: { fontFamily: fonts.medium, fontSize: 13 },
});
