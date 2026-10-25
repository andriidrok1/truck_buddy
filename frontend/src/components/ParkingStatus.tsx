import { Image } from "expo-image";
import { useState } from "react";
import { Modal, Pressable, StyleSheet, Text, View } from "react-native";
import Animated, { ZoomIn } from "react-native-reanimated";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { apiUrl } from "../api";
import { fonts, radius, spacing, useTheme, type ThemeColors } from "../theme";
import type { ParkingCheck, ParkingSpot } from "../types";
import { Icon } from "./Icon";
import { PressableScale } from "./PressableScale";

function look(c: ThemeColors, v: ParkingCheck["verdict"]) {
  if (v === "listed") return { icon: "check-circle", color: c.success };
  if (v === "likely") return { icon: "satellite-variant", color: c.success };
  if (v === "unlikely") return { icon: "alert-circle", color: c.error };
  return { icon: "help-circle", color: c.warning };
}

/** Entry (E) / park (P) markers drawn over the satellite image at normalized positions. */
function SpotMarkers({ spots, size }: { spots: ParkingSpot[]; size: number }) {
  const { colors } = useTheme();
  return (
    <>
      {spots.map((s, i) => {
        const bg = s.kind === "entry" ? colors.info : colors.success;
        return (
          <View
            key={`${s.kind}-${i}`}
            testID={`parking-spot-${s.kind}-${i}`}
            pointerEvents="none"
            style={[
              styles.marker,
              {
                width: size,
                height: size,
                borderRadius: size / 2,
                left: `${s.x * 100}%`,
                top: `${s.y * 100}%`,
                marginLeft: -size / 2,
                marginTop: -size / 2,
                backgroundColor: bg,
                borderColor: colors.surface,
              },
            ]}
          >
            <Text style={[styles.markerText, { color: s.kind === "entry" ? colors.onInfo : colors.onSuccess, fontSize: size * 0.5 }]}>
              {s.kind === "entry" ? "E" : "P"}
            </Text>
          </View>
        );
      })}
    </>
  );
}

function Tip({ icon, text, testID }: { icon: string; text: string; testID: string }) {
  const { colors } = useTheme();
  return (
    <View style={styles.tipRow}>
      <Icon name={icon} size={16} color={colors.onSurfaceSecondary} />
      <Text testID={testID} numberOfLines={2} style={[styles.tip, { color: colors.onSurface }]}>
        {text}
      </Text>
    </View>
  );
}

/** Parking verdict, how to enter / where to park, and the satellite thumbnail (tap to enlarge). */
export function ParkingStatus({ parking, idSuffix = "" }: { parking?: ParkingCheck | null; idSuffix?: string }) {
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const [open, setOpen] = useState(false);

  if (!parking) {
    return (
      <Text testID={`parking-status-label${idSuffix}`} style={[styles.reason, { color: colors.muted }]}>
        Truck parking unclear
      </Text>
    );
  }
  const { icon, color } = look(colors, parking.verdict);
  const uri = parking.image_path ? apiUrl(parking.image_path) : null;
  const spots = parking.spots ?? [];

  return (
    <View testID={`parking-status${idSuffix}`} style={styles.row}>
      <View style={styles.textCol}>
        <Animated.View key={`pk-${parking.verdict}-${parking.label}`} entering={ZoomIn.springify().damping(14)} style={styles.labelRow}>
          <Icon name={icon} size={18} color={color} />
          <Text testID={`parking-status-label${idSuffix}`} numberOfLines={2} style={[styles.label, { color: colors.onSurface }]}>
            {parking.label}
          </Text>
        </Animated.View>
        {!!parking.entry_tip && <Tip icon="login-variant" text={parking.entry_tip} testID={`parking-entry-tip${idSuffix}`} />}
        {!!parking.park_tip && <Tip icon="parking" text={parking.park_tip} testID={`parking-park-tip${idSuffix}`} />}
        {!parking.entry_tip && !parking.park_tip && !!parking.reason && (
          <Text testID={`parking-status-reason${idSuffix}`} numberOfLines={2} style={[styles.reason, { color: colors.onSurfaceSecondary }]}>
            {parking.reason}
          </Text>
        )}
        {!!uri && (
          <Text testID={`parking-imagery-note${idSuffix}`} style={[styles.reason, { color: colors.muted }]}>
            Satellite imagery, may be dated
          </Text>
        )}
      </View>

      {uri && (
        <PressableScale testID={`parking-satellite-thumb${idSuffix}`} accessibilityLabel="Open satellite view" onPress={() => setOpen(true)}>
          <View style={styles.thumbWrap}>
            {/* Icon shows through until the image loads */}
            <View pointerEvents="none" style={[styles.thumbIcon, { backgroundColor: colors.surfaceTertiary }]}>
              <Icon name="satellite-variant" size={20} color={colors.muted} />
            </View>
            <Image source={{ uri }} style={styles.thumb} contentFit="cover" transition={150} />
            <SpotMarkers spots={spots} size={14} />
          </View>
        </PressableScale>
      )}

      {uri && (
        <Modal visible={open} transparent animationType="fade" onRequestClose={() => setOpen(false)}>
          <Pressable
            testID={`parking-satellite-modal${idSuffix}`}
            style={[styles.backdrop, { backgroundColor: colors.surfaceInverse, paddingTop: insets.top, paddingBottom: insets.bottom }]}
            onPress={() => setOpen(false)}
          >
            <View style={styles.bigWrap}>
              <Image source={{ uri }} style={styles.big} contentFit="contain" />
              <View pointerEvents="none" style={[styles.crosshair, { borderColor: colors.onSurfaceInverse }]} />
              <SpotMarkers spots={spots} size={28} />
            </View>
            <Text style={[styles.caption, { color: colors.onSurfaceInverse }]}>{parking.label}</Text>
            {!!parking.entry_tip && (
              <Text style={[styles.modalTip, { color: colors.onSurfaceInverse }]}>E · {parking.entry_tip}</Text>
            )}
            {!!parking.park_tip && (
              <Text style={[styles.modalTip, { color: colors.onSurfaceInverse }]}>P · {parking.park_tip}</Text>
            )}
            <Text style={[styles.attrib, { color: colors.onSurfaceTertiary }]}>Imagery © Esri · Tap to close</Text>
          </Pressable>
        </Modal>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: "row", alignItems: "center", gap: spacing.md, marginTop: spacing.sm },
  textCol: { flex: 1, gap: 4 },
  labelRow: { flexDirection: "row", alignItems: "center", gap: 6 },
  label: { fontFamily: fonts.semibold, fontSize: 14, flexShrink: 1 },
  reason: { fontFamily: fonts.regular, fontSize: 12, marginTop: 2 },
  tipRow: { flexDirection: "row", alignItems: "flex-start", gap: 6 },
  tip: { fontFamily: fonts.medium, fontSize: 13, lineHeight: 18, flexShrink: 1 },
  thumbWrap: { width: 72, height: 72, borderRadius: radius.md, overflow: "hidden" },
  thumb: { width: 72, height: 72 },
  thumbIcon: { ...StyleSheet.absoluteFill, alignItems: "center", justifyContent: "center" },
  marker: { position: "absolute", borderWidth: 1.5, alignItems: "center", justifyContent: "center" },
  markerText: { fontFamily: fonts.extrabold },
  backdrop: { flex: 1, alignItems: "center", justifyContent: "center", gap: spacing.sm, paddingHorizontal: spacing.lg },
  bigWrap: { width: "100%", aspectRatio: 1, alignItems: "center", justifyContent: "center" },
  big: { width: "100%", height: "100%" },
  crosshair: { position: "absolute", width: 28, height: 28, borderRadius: 14, borderWidth: 2 },
  caption: { fontFamily: fonts.bold, fontSize: 16, marginTop: spacing.sm },
  modalTip: { fontFamily: fonts.medium, fontSize: 14, textAlign: "center" },
  attrib: { fontFamily: fonts.regular, fontSize: 12, marginTop: spacing.sm },
});
