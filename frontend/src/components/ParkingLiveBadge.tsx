import { StyleSheet, Text, View } from "react-native";

import { parkingBadgeText, showsParkingBadge, TREND_ICON } from "../geo/parkingBadge";
import { fonts, radius, useTheme } from "../theme";
import type { Place } from "../types";
import { Icon } from "./Icon";

type Props = { place: Place; idSuffix?: string; now?: number };

/** "56/68 open · clearing" pill from TPIMS / NTAD counts; grey "as of 7:42 pm" once the count is stale. */
export function ParkingLiveBadge({ place, idSuffix = "", now }: Props) {
  const { colors } = useTheme();
  if (!showsParkingBadge(place)) return null;
  const badge = parkingBadgeText(place, now);
  if (!badge) return null;

  const tint = badge.tone === "low" ? colors.warning : badge.tone === "stale" ? colors.muted : colors.success;
  const icon = badge.tone === "low" ? "alert-circle-outline" : badge.trend ? TREND_ICON[badge.trend] : "parking";

  return (
    <View testID={`parking-live${idSuffix}`} style={styles.row}>
      <View style={[styles.pill, { backgroundColor: colors.surface, borderColor: tint }]}>
        <Icon name={icon} size={14} color={tint} />
        <Text testID={`parking-live-text${idSuffix}`} numberOfLines={1} style={[styles.text, { color: badge.tone === "stale" ? colors.muted : colors.onSurface }]}>
          {badge.text}
        </Text>
      </View>
      {!!badge.asOf && (
        <Text testID={`parking-live-asof${idSuffix}`} style={[styles.asOf, { color: colors.muted }]}>
          {badge.asOf}
        </Text>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: "row", alignItems: "center", gap: 6, marginTop: 4, flexWrap: "wrap" },
  pill: { flexDirection: "row", alignItems: "center", gap: 4, height: 24, paddingHorizontal: 8, borderRadius: radius.pill, borderWidth: 1 },
  text: { fontFamily: fonts.semibold, fontSize: 12 },
  asOf: { fontFamily: fonts.regular, fontSize: 12 },
});
