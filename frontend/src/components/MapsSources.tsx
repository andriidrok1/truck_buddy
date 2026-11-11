import { Linking, Pressable, StyleSheet, Text, View } from "react-native";

import { fonts, spacing, useTheme } from "../theme";
import type { MapsSource } from "../types";
import { t } from "../i18n";

/**
 * The Google Maps places a grounded answer came from ("what do drivers say about it?"). Google's terms for
 * Grounding with Google Maps want them right under the answer, one tap away, attributed to "Google Maps" written
 * exactly so (not translated, one line).
 */
export function MapsSources({ sources, center = false }: { sources?: MapsSource[] | null; center?: boolean }) {
  const { colors } = useTheme();
  if (!sources?.length) return null;
  return (
    <View testID="maps-sources" style={[styles.row, center && styles.center]}>
      <Text style={[styles.label, { color: colors.onSurfaceSecondary }]} numberOfLines={1}>
        Google Maps
      </Text>
      {sources.slice(0, 3).map((s) => (
        <Pressable
          key={s.url}
          testID="maps-source-link"
          accessibilityRole="link"
          accessibilityLabel={t("{name} on Google Maps", { name: s.name })}
          hitSlop={8}
          onPress={() => Linking.openURL(s.url).catch(() => {})}
        >
          <Text style={[styles.link, { color: colors.info }]} numberOfLines={1}>
            {s.name}
          </Text>
        </Pressable>
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: "row", flexWrap: "wrap", alignItems: "center", gap: spacing.sm, marginTop: spacing.xs },
  center: { justifyContent: "center" },
  label: { fontFamily: fonts.semibold, fontSize: 12 },
  link: { fontFamily: fonts.medium, fontSize: 13, textDecorationLine: "underline", maxWidth: 220 },
});
