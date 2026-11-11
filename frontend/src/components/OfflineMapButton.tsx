import { Pressable, StyleSheet, View } from "react-native";

import type { OfflineMapState } from "../hooks/useOfflineMap";
import { Text } from "../i18n/Text";
import { formatBytes } from "../map/bounds";
import { fonts, radius, spacing, useTheme } from "../theme";
import { Icon } from "./Icon";
import { t } from "../i18n";

// Moved verbatim from CopilotSheet.tsx.
/** "Download for offline · ~84 MB" / "Downloading · 42%" / "Saved · 84 MB" / "Download failed · try again". */
export function offlineMapLabel(s: OfflineMapState): string {
  switch (s.kind) {
    case "none":
      return `Download for offline · ~${formatBytes(s.estimateBytes)}`;
    case "downloading":
      return `Downloading · ${Math.round(s.percentage)}%${s.bytes ? ` · ${formatBytes(s.bytes)}` : ""}`;
    case "saved":
      return `Saved · ${formatBytes(s.bytes)}`;
    case "failed":
      return `Download failed · try again (~${formatBytes(s.estimateBytes)})`;
  }
}

export function OfflineMapButton({ state, onPress }: { state: OfflineMapState; onPress?: () => void }) {
  const { colors } = useTheme();
  const busy = state.kind === "downloading";
  const saved = state.kind === "saved";
  return (
    <Pressable
      testID="offline-map-button"
      accessibilityLabel={t(offlineMapLabel(state))}
      accessibilityState={{ disabled: busy || saved }}
      disabled={busy || saved}
      onPress={onPress}
      hitSlop={8}
      style={[styles.offlineBtn, { borderColor: saved ? colors.success : colors.info }]}
    >
      <Icon name={saved ? "check-circle-outline" : busy ? "progress-download" : "download-outline"} size={16} color={saved ? colors.success : colors.info} />
      <Text testID="offline-map-label" style={[styles.otherText, { color: saved ? colors.success : colors.info }]}>
        {offlineMapLabel(state)}
      </Text>
      {busy && (
        <View style={[styles.progressTrack, { backgroundColor: colors.surfaceTertiary }]}>
          <View style={[styles.progressFill, { backgroundColor: colors.info, width: `${Math.max(2, Math.min(100, state.percentage))}%` }]} />
        </View>
      )}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  offlineBtn: { flexDirection: "row", flexWrap: "wrap", alignItems: "center", alignSelf: "flex-start", gap: 6, minHeight: 48, paddingHorizontal: spacing.md, paddingVertical: 4, borderRadius: radius.pill, borderWidth: 1, marginTop: spacing.xs },
  progressTrack: { width: "100%", height: 3, borderRadius: 2, overflow: "hidden" },
  progressFill: { height: 3 },
  otherText: { fontFamily: fonts.bold, fontSize: 14 },
});
