import { Modal, Pressable, StyleSheet, View } from "react-native";
import { SafeAreaProvider, useSafeAreaInsets } from "react-native-safe-area-context";

import { t } from "../i18n";
import { Text } from "../i18n/Text";
import type { MapView } from "../state/settingsStore";
import { fonts, radius, shadow, spacing, useTheme } from "../theme";
import { Icon } from "./Icon";
import { PressableScale } from "./PressableScale";

type Option = { view: MapView; label: string; icon: string };

/** Satellite is offered where the map engine has imagery (react-native-maps: Apple / Google); the MapLibre build
 *  passes satellite=false. */
export function mapViewOptions(satellite: boolean): Option[] {
  return [
    { view: "map", label: "Map", icon: "map-outline" },
    ...(satellite ? [{ view: "satellite" as const, label: "Satellite", icon: "satellite-variant" }] : []),
    { view: "weather", label: "Weather", icon: "weather-pouring" },
  ];
}

type Props = {
  visible: boolean;
  value: MapView;
  satellite: boolean;
  onPick: (v: MapView) => void;
  onClose: () => void;
};

/** "Map view": three big tiles (Figma W2). The pick applies at once and is remembered; by voice the same works
 *  with "show the weather" / "satellite" / "normal map". */
export function MapViewSheet({ visible, value, satellite, onPick, onClose }: Props) {
  const { colors } = useTheme();
  return (
    <Modal visible={visible} transparent animationType="fade" onRequestClose={onClose} statusBarTranslucent>
      {/* A Modal is its own window: the insets come from a provider inside it (see HowToSheet). */}
      <SafeAreaProvider>
        <Pressable testID="map-view-backdrop" accessibilityLabel={t("Close")} onPress={onClose} style={[styles.backdrop, { backgroundColor: colors.scrim }]} />
        <Body value={value} satellite={satellite} onPick={onPick} onClose={onClose} />
      </SafeAreaProvider>
    </Modal>
  );
}

function Body({ value, satellite, onPick, onClose }: Omit<Props, "visible">) {
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  return (
    <View testID="map-view-sheet" style={[styles.sheet, shadow(colors, 2), { backgroundColor: colors.surface, paddingBottom: insets.bottom + spacing.lg }]}>
      <View style={styles.head}>
        <Text style={[styles.title, { color: colors.onSurface }]}>Map view</Text>
        <PressableScale testID="map-view-close" accessibilityRole="button" accessibilityLabel={t("Close")} onPress={onClose} style={[styles.close, { backgroundColor: colors.surfaceSecondary }]}>
          <Icon name="close" size={22} color={colors.onSurface} />
        </PressableScale>
      </View>
      <View style={styles.row}>
        {mapViewOptions(satellite).map((o) => {
          const on = o.view === value;
          return (
            <PressableScale
              key={o.view}
              testID={`map-view-${o.view}`}
              accessibilityRole="button"
              accessibilityState={{ selected: on }}
              accessibilityLabel={t(o.label)}
              onPress={() => onPick(o.view)}
              containerStyle={styles.cell}
              style={[styles.tile, { backgroundColor: colors.surfaceSecondary, borderColor: on ? colors.info : "transparent" }]}
            >
              <Icon name={o.icon} size={40} color={on ? colors.info : colors.onSurface} />
              <Text style={[styles.label, { color: on ? colors.info : colors.onSurface, fontFamily: on ? fonts.bold : fonts.semibold }]}>{o.label}</Text>
              {on && (
                <View style={[styles.check, { backgroundColor: colors.info, borderColor: colors.surface }]}>
                  <Icon name="check" size={16} color={colors.onInfo} />
                </View>
              )}
            </PressableScale>
          );
        })}
      </View>
      <View style={[styles.hint, { backgroundColor: colors.surfaceSecondary }]}>
        <Icon name="microphone" size={22} color={colors.info} />
        <Text style={[styles.hintText, { color: colors.onSurface }]}>Or just say “show the weather”</Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  backdrop: { position: "absolute", top: 0, left: 0, right: 0, bottom: 0 },
  sheet: { position: "absolute", left: 0, right: 0, bottom: 0, borderTopLeftRadius: 28, borderTopRightRadius: 28, paddingTop: spacing.lg, paddingHorizontal: spacing.xl, gap: spacing.lg },
  head: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  title: { fontFamily: fonts.extrabold, fontSize: 22 },
  close: { width: 48, height: 48, borderRadius: 24, alignItems: "center", justifyContent: "center" },
  row: { flexDirection: "row", gap: spacing.md },
  cell: { flex: 1 },
  tile: { height: 120, borderRadius: radius.lg, borderWidth: 3, alignItems: "center", justifyContent: "center", gap: spacing.sm },
  // "Weather" already means lowercase "погода" mid-sentence in Russian; on a tile it starts with a capital.
  label: { fontSize: 16, textTransform: "capitalize" },
  check: { position: "absolute", top: 8, right: 8, width: 28, height: 28, borderRadius: 14, borderWidth: 2, alignItems: "center", justifyContent: "center" },
  hint: { minHeight: 56, borderRadius: radius.lg, flexDirection: "row", alignItems: "center", gap: spacing.md, paddingHorizontal: spacing.lg },
  hintText: { fontFamily: fonts.semibold, fontSize: 15, flex: 1 },
});
