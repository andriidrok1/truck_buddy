// Saved offline map packs on the truck profile screen, each with its size and a delete button.
import { View } from "react-native";
import { Text } from "../i18n/Text";

import { removePack, usePacks } from "../hooks/useOfflineMap";
import { formatBytes } from "../map/bounds";
import { useTheme } from "../theme";
import { Icon } from "./Icon";
import { PressableScale } from "./PressableScale";
import { styles } from "./truckProfileStyles";
import { t, useLang } from "../i18n";
import { numericDate } from "../utils/arrivalClock";

/** MapLibre builds only: with the Apple provider there is no pack to list and nothing to delete. */
export function OfflineMapsSection() {
  const { colors } = useTheme();
  const offlineMaps = usePacks();
  const lang = useLang(); // the download date in the app's language, not the phone's (QA 2026-11-09, S16)
  if (!offlineMaps.available) return null;
  return (
    <>
      <Text style={[styles.section, { color: colors.onSurface }]}>Offline maps</Text>
      <View testID="offline-maps-section" style={[styles.eldBox, { backgroundColor: colors.surfaceSecondary }]}>
        {offlineMaps.packs.length === 0 ? (
          <Text style={[styles.switchSub, { color: colors.onSurfaceSecondary }]}>
            No maps saved. Set a route and tap “Download for offline” on its card; the last three routes are kept.
          </Text>
        ) : (
          offlineMaps.packs.map((p) => (
            <View key={p.name} testID={`offline-pack-${p.name}`} style={styles.eldStatusRow}>
              <Icon name={p.complete ? "map-check-outline" : "progress-download"} size={18} color={p.complete ? colors.success : colors.muted} />
              <View style={styles.flex}>
                <Text style={[styles.switchTitle, { color: colors.onSurface }]} numberOfLines={1}>
                  {p.name}
                </Text>
                <Text style={[styles.switchSub, { color: colors.onSurfaceSecondary }]}>
                  {p.complete ? formatBytes(p.bytes) : `${Math.round(p.percentage)}% · ${formatBytes(p.bytes)}`}
                  {p.createdAt ? ` · ${numericDate(new Date(p.createdAt), lang)}` : ""}
                </Text>
              </View>
              <PressableScale testID={`offline-pack-delete-${p.name}`} accessibilityLabel={t("Delete {name}", { name: p.name })} onPress={() => removePack(p.name)} style={styles.packDelete}>
                <Icon name="delete-outline" size={22} color={colors.error} />
              </PressableScale>
            </View>
          ))
        )}
        <Text style={[styles.switchSub, { color: colors.muted }]}>Map data © OpenFreeMap © OpenMapTiles © OpenStreetMap contributors.</Text>
      </View>
    </>
  );
}
