import { Modal, Pressable, StyleSheet, View } from "react-native";
import { SafeAreaProvider, useSafeAreaInsets } from "react-native-safe-area-context";

import { t } from "../i18n";
import { Text } from "../i18n/Text";
import { frameClock } from "../map/mapView";
import { fonts, radius, shadow, spacing, useTheme } from "../theme";
import { detail, KIND_ICON, type Chip } from "../weather/routeWeather";
import { Icon } from "./Icon";
import { PressableScale } from "./PressableScale";
import { KIND_COLOR } from "./WeatherChip";

const TITLE: Record<string, string> = {
  ice: "Ice risk", snow: "Snow", wind: "Strong gusts", fog: "Fog", rain: "Rain", cloud: "Cloudy", clear: "Clear sky",
};

/** What the driver hears from "Read it to me": one sentence in the driver's language. */
export function spokenLine(c: Chip): string {
  const where = c.place?.split(",")[0] ?? t("that point");
  const parts = [detail(c).toLowerCase()];
  if (c.temp_f != null) parts.push(t("{n} degrees", { n: c.temp_f }));
  return t("Around {when} near {where}: {what}.", { when: frameClock(c.eta) ?? "", where, what: parts.join(", ") });
}

type Props = { chip: Chip | null; onClose: () => void; onRead: (c: Chip) => void };

/** Details of one point of the route (Figma V3 / W4): what, where, when the truck gets there, the alert. */
export function WeatherPointSheet({ chip, onClose, onRead }: Props) {
  const { colors } = useTheme();
  return (
    <Modal visible={!!chip} transparent animationType="fade" onRequestClose={onClose} statusBarTranslucent>
      <SafeAreaProvider>
        <Pressable testID="wx-sheet-backdrop" accessibilityLabel={t("Close")} onPress={onClose} style={[styles.backdrop, { backgroundColor: colors.scrim }]} />
        {chip && <Body chip={chip} onClose={onClose} onRead={onRead} />}
      </SafeAreaProvider>
    </Modal>
  );
}

function Body({ chip, onClose, onRead }: { chip: Chip; onClose: () => void; onRead: (c: Chip) => void }) {
  const { colors, scheme } = useTheme();
  const insets = useSafeAreaInsets();
  const tint = (KIND_COLOR[chip.kind] ?? KIND_COLOR.cloud)[scheme === "dark" ? "dark" : "light"];
  const rows: { icon: string; color: string; title: string; sub?: string }[] = [];
  rows.push({ icon: "clock-outline", color: colors.onSurface, title: t("You get there around {time}", { time: frameClock(chip.eta) ?? "" }), sub: chip.place ?? undefined });
  if (chip.temp_f != null) rows.push({ icon: "thermometer", color: colors.onSurface, title: `${chip.temp_f}°F`, sub: chip.ice_risk ? t("Near freezing on a wet road: bridges ice first") : undefined });
  if (chip.gust_mph != null) rows.push({ icon: "weather-windy", color: KIND_COLOR.wind.light, title: t("Gusts to {gust} mph", { gust: String(chip.gust_mph) }), sub: chip.reasons?.includes("gusts") ? t("Crosswinds can tip an empty trailer") : undefined });
  if (chip.vis_mi != null && chip.vis_mi < 3) rows.push({ icon: "weather-fog", color: colors.onSurfaceSecondary, title: t("Visibility {vis} mi", { vis: String(chip.vis_mi) }) });
  if (chip.alert) rows.push({ icon: "alert", color: colors.warning, title: chip.alert.event, sub: chip.alert.area });
  return (
    <View testID="wx-sheet" style={[styles.sheet, shadow(colors, 2), { backgroundColor: colors.surface, paddingBottom: insets.bottom + spacing.lg }]}>
      <View style={styles.head}>
        <View style={[styles.badge, { backgroundColor: colors.surfaceSecondary }]}>
          <Icon name={KIND_ICON[chip.kind] ?? "weather-cloudy"} size={28} color={tint} />
        </View>
        <View style={styles.headText}>
          <Text style={[styles.title, { color: colors.onSurface }]}>{chip.alert?.event ?? TITLE[chip.kind] ?? chip.kind}</Text>
          <Text style={[styles.sub, { color: colors.onSurfaceSecondary }]}>{t("NWS forecast for when you get there")}</Text>
        </View>
        <PressableScale testID="wx-sheet-close" accessibilityRole="button" accessibilityLabel={t("Close")} onPress={onClose} style={[styles.close, { backgroundColor: colors.surfaceSecondary }]}>
          <Icon name="close" size={22} color={colors.onSurface} />
        </PressableScale>
      </View>
      {rows.map((r) => (
        <View key={r.title} style={styles.row}>
          <Icon name={r.icon} size={24} color={r.color} />
          <View style={styles.rowText}>
            <Text style={[styles.rowTitle, { color: colors.onSurface }]}>{r.title}</Text>
            {!!r.sub && <Text style={[styles.rowSub, { color: colors.onSurfaceSecondary }]}>{r.sub}</Text>}
          </View>
        </View>
      ))}
      <PressableScale testID="wx-sheet-read" accessibilityRole="button" onPress={() => onRead(chip)} style={[styles.btn, { backgroundColor: colors.surfaceSecondary }]}>
        <Icon name="volume-high" size={22} color={colors.onSurface} />
        <Text style={[styles.btnText, { color: colors.onSurface }]}>Read it to me</Text>
      </PressableScale>
    </View>
  );
}

const styles = StyleSheet.create({
  backdrop: { position: "absolute", top: 0, left: 0, right: 0, bottom: 0 },
  sheet: { position: "absolute", left: 0, right: 0, bottom: 0, borderTopLeftRadius: 28, borderTopRightRadius: 28, paddingTop: spacing.lg, paddingHorizontal: spacing.xl, gap: spacing.md },
  head: { flexDirection: "row", alignItems: "center", gap: spacing.md },
  badge: { width: 52, height: 52, borderRadius: 26, alignItems: "center", justifyContent: "center" },
  headText: { flex: 1 },
  title: { fontFamily: fonts.extrabold, fontSize: 21 },
  sub: { fontFamily: fonts.semibold, fontSize: 14 },
  close: { width: 48, height: 48, borderRadius: 24, alignItems: "center", justifyContent: "center" },
  row: { flexDirection: "row", alignItems: "flex-start", gap: spacing.md },
  rowText: { flex: 1 },
  rowTitle: { fontFamily: fonts.bold, fontSize: 16 },
  rowSub: { fontFamily: fonts.medium, fontSize: 14 },
  btn: { height: 56, borderRadius: radius.pill, flexDirection: "row", gap: spacing.sm, alignItems: "center", justifyContent: "center", marginTop: spacing.sm },
  btnText: { fontFamily: fonts.bold, fontSize: 17 },
});
