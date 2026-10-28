import { Image } from "expo-image";
import { useState } from "react";
import { Modal, Pressable, StyleSheet, Text, View } from "react-native";
import Animated, { ZoomIn } from "react-native-reanimated";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { apiUrl } from "../api";
import { authHeader } from "../auth";
import { fonts, radius, spacing, useTheme, type ThemeColors } from "../theme";
import type { LatLng, ParkingCheck, ParkingSpot, Place } from "../types";
import { routeTail } from "../utils/entry";
import { openStreetView } from "../utils/navLinks";
import { Icon } from "./Icon";
import ParkingSatMap from "./ParkingSatMap";
import { PressableScale } from "./PressableScale";
import { SpotBadge } from "./SpotBadge";

function look(c: ThemeColors, v: ParkingCheck["verdict"]) {
  if (v === "listed") return { icon: "check-circle", color: c.success };
  if (v === "likely") return { icon: "satellite-variant", color: c.success };
  if (v === "unlikely") return { icon: "alert-circle", color: c.error };
  return { icon: "help-circle", color: c.warning };
}

/** Entry (E) / park (P) markers drawn over the satellite image at normalized positions. */
function SpotMarkers({ spots, size }: { spots: ParkingSpot[]; size: number }) {
  return (
    <>
      {spots.map((s, i) => (
        <View
          key={`${s.kind}-${i}`}
          testID={`parking-spot-${s.kind}-${i}`}
          pointerEvents="none"
          style={[styles.marker, { left: `${s.x * 100}%`, top: `${s.y * 100}%`, marginLeft: -size / 2, marginTop: -size / 2 }]}
        >
          <SpotBadge kind={s.kind} size={size} />
        </View>
      ))}
    </>
  );
}

/** Center of the satellite frame, read from its image path (`/satellite?lat=..&lng=..`). */
function frameCenter(path: string): LatLng | null {
  const lat = Number(/[?&]lat=(-?[\d.]+)/.exec(path)?.[1]);
  const lng = Number(/[?&]lng=(-?[\d.]+)/.exec(path)?.[1]);
  return Number.isFinite(lat) && Number.isFinite(lng) ? { lat, lng } : null;
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

type Props = {
  parking?: ParkingCheck | null;
  idSuffix?: string;
  /** The checked place (draws its pin on the zoomable map). */
  place?: Place | null;
  /** Active route geometry; its last ~300 m is drawn on the zoomable map. */
  route?: [number, number][] | null;
};

/** Parking verdict, how to enter / where to park, and the satellite thumbnail (tap for a zoomable map). */
export function ParkingStatus({ parking, idSuffix = "", place, route }: Props) {
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
  const center = parking.image_path ? frameCenter(parking.image_path) : null;
  const close = () => setOpen(false);

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
            <Image source={{ uri, headers: authHeader() }} style={styles.thumb} contentFit="cover" transition={150} />
            <SpotMarkers spots={spots} size={14} />
          </View>
        </PressableScale>
      )}

      {uri && center && (
        <Modal visible={open} animationType="slide" presentationStyle="fullScreen" statusBarTranslucent onRequestClose={close}>
          <View testID={`parking-satellite-modal${idSuffix}`} style={[styles.modal, { backgroundColor: colors.surfaceInverse }]}>
            <View style={styles.mapWrap}>
              {open && <ParkingSatMap center={center} place={place} spots={spots} tail={route?.length ? routeTail(route) : []} />}
              <PressableScale
                testID={`parking-satellite-close${idSuffix}`}
                accessibilityLabel="Close satellite view"
                onPress={close}
                containerStyle={[styles.closeWrap, { top: insets.top + spacing.sm }]}
                style={[styles.closeBtn, { backgroundColor: colors.surface }]}
              >
                <Icon name="close" size={22} color={colors.onSurface} />
              </PressableScale>
            </View>
            <Pressable
              testID={`parking-satellite-footer${idSuffix}`}
              accessibilityLabel="Tap to close"
              onPress={close}
              style={[styles.footer, { paddingBottom: insets.bottom + spacing.md }]}
            >
              <Text testID={`parking-satellite-verdict${idSuffix}`} style={[styles.caption, { color: colors.onSurfaceInverse }]}>
                {parking.label}
              </Text>
              {!!parking.entry_tip && (
                <Text style={[styles.modalTip, { color: colors.onSurfaceInverse }]}>E · {parking.entry_tip}</Text>
              )}
              {!!parking.park_tip && (
                <Text style={[styles.modalTip, { color: colors.onSurfaceInverse }]}>P · {parking.park_tip}</Text>
              )}
              <PressableScale
                testID={`parking-open-google${idSuffix}`}
                accessibilityLabel="Open in Google Maps"
                onPress={() => openStreetView(place?.lat ?? center.lat, place?.lng ?? center.lng)}
                style={[styles.gmBtn, { backgroundColor: colors.surface }]}
              >
                <Icon name="google-maps" size={20} color={colors.info} />
                <Text style={[styles.gmText, { color: colors.onSurface }]}>Open in Google Maps</Text>
              </PressableScale>
              <Text style={[styles.attrib, { color: colors.onSurfaceTertiary }]}>Imagery © Esri, may be dated · Tap to close</Text>
            </Pressable>
          </View>
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
  marker: { position: "absolute" },
  modal: { flex: 1 },
  mapWrap: { flex: 1, overflow: "hidden" },
  closeWrap: { position: "absolute", right: spacing.md },
  closeBtn: { width: 44, height: 44, borderRadius: 22, alignItems: "center", justifyContent: "center" },
  footer: { alignItems: "center", gap: spacing.sm, paddingHorizontal: spacing.lg, paddingTop: spacing.md },
  gmBtn: { flexDirection: "row", alignItems: "center", gap: spacing.sm, minHeight: 48, paddingHorizontal: spacing.lg, borderRadius: radius.lg, marginTop: spacing.xs },
  gmText: { fontFamily: fonts.semibold, fontSize: 15 },
  caption: { fontFamily: fonts.bold, fontSize: 16, textAlign: "center" },
  modalTip: { fontFamily: fonts.medium, fontSize: 14, textAlign: "center" },
  attrib: { fontFamily: fonts.regular, fontSize: 12, marginTop: spacing.sm },
});
