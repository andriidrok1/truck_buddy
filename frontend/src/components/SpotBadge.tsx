import { StyleSheet, Text, View } from "react-native";

import { fonts, useTheme } from "../theme";
import type { ParkingSpot } from "../types";

/** Round E (entry, blue) / P (park, green) badge. */
export function SpotBadge({ kind, size }: { kind: ParkingSpot["kind"]; size: number }) {
  const { colors } = useTheme();
  const entry = kind === "entry";
  return (
    <View
      style={[
        styles.badge,
        {
          width: size,
          height: size,
          borderRadius: size / 2,
          backgroundColor: entry ? colors.info : colors.success,
          borderColor: colors.surface,
        },
      ]}
    >
      <Text style={[styles.text, { color: entry ? colors.onInfo : colors.onSuccess, fontSize: size * 0.5 }]}>
        {entry ? "E" : "P"}
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  badge: { borderWidth: 1.5, alignItems: "center", justifyContent: "center" },
  text: { fontFamily: fonts.extrabold },
});
