import { Pressable, StyleSheet, Text, View } from "react-native";
import Animated, { FadeIn, FadeInDown } from "react-native-reanimated";

import { fonts, radius, shadow, spacing, useTheme } from "../theme";
import type { ActiveRoute, ParkingState, Phase, Place } from "../types";
import { Avatar } from "./Avatar";
import { Icon } from "./Icon";
import { OptionsList } from "./OptionsList";
import { ParkingStatus } from "./ParkingStatus";
import { haptic, PressableScale } from "./PressableScale";
import { Waveform } from "./Waveform";

type Props = {
  phase: Phase;
  speaking: boolean;
  onShowOptions: () => void;
  thinkingText?: string;
  options: Place[];
  parking: Record<string, ParkingState>;
  onOptionParking: (p: Place) => void;
  onOptionRoute: (p: Place) => void;
  active: ActiveRoute | null;
  bottomInset: number;
  onAvatarPress: () => void;
  onClose: () => void;
  onEndRoute: () => void;
  onOpenGoogle: () => void;
  onOpenWaze: () => void;
  onLayout: (h: number) => void;
};

function SideButton({
  icon,
  label,
  testID,
  onPress,
  danger,
}: {
  icon: string;
  label: string;
  testID: string;
  onPress?: () => void;
  danger?: boolean;
}) {
  const { colors } = useTheme();
  return (
    <PressableScale testID={testID} accessibilityLabel={label} onPress={onPress} style={styles.side} haptic={danger ? "heavy" : "light"}>
      <View style={[styles.sideCircle, { backgroundColor: danger ? colors.error : colors.surfaceSecondary }]}>
        <Icon name={icon} size={26} color={danger ? colors.onError : colors.onSurface} />
      </View>
      <Text style={[styles.sideLabel, { color: danger ? colors.error : colors.onSurfaceSecondary }]}>{label}</Text>
    </PressableScale>
  );
}

export function CopilotSheet(props: Props) {
  const { phase, speaking, onShowOptions, thinkingText, options, parking, onOptionParking, onOptionRoute, active, bottomInset, onAvatarPress, onClose, onEndRoute, onOpenGoogle, onOpenWaze, onLayout } = props;
  const { colors } = useTheme();
  const navigating = phase === "navigating";

  const caption =
    phase === "listening"
      ? "Tap to stop"
      : phase === "thinking"
        ? "Thinking…"
        : speaking
          ? "Speaking…"
          : navigating
            ? "Talk to copilot"
            : "Tap to talk";

  return (
    <View
      testID="copilot-sheet"
      onLayout={(e) => onLayout(e.nativeEvent.layout.height)}
      style={[styles.sheet, shadow(colors, 2), { backgroundColor: colors.surface, paddingBottom: bottomInset + spacing.lg }]}
    >
      <View style={[styles.handle, { backgroundColor: colors.surfaceTertiary }]} />

      <Animated.View key={`head-${phase}`} entering={FadeIn.duration(220)} style={styles.header}>
        {phase === "ready" && (
          <Text testID="sheet-title" style={[styles.title, { color: colors.onSurface }]}>
            Where to next?
          </Text>
        )}
        {phase === "listening" && (
          <View style={styles.listenRow}>
            <Text testID="sheet-title" style={[styles.title, { color: colors.onSurface }]}>
              I&apos;m listening
            </Text>
            <Waveform />
          </View>
        )}
        {phase === "thinking" && (
          <Text testID="sheet-title" style={[styles.title, { color: colors.onSurface }]}>
            {thinkingText ?? "Finding the best spot…"}
          </Text>
        )}
        {phase === "options" && (
          <OptionsList options={options} parking={parking} onParking={onOptionParking} onRoute={onOptionRoute} />
        )}
        {navigating && active && (
          <View style={styles.navInfo}>
            <Text testID="route-summary" style={[styles.eta, { color: colors.onSurface }]}>
              {active.route.duration_min} min · {active.route.distance_mi.toFixed(1)} mi
            </Text>
            <Text testID="route-place-name" numberOfLines={1} style={[styles.placeName, { color: colors.onSurface }]}>
              {active.place.name}
            </Text>
            <View style={styles.tagRow}>
              <Icon name={active.place.tags.length ? "truck" : "map-marker-outline"} size={18} color={colors.onSurfaceSecondary} />
              <Text testID="route-place-tags" numberOfLines={1} style={[styles.tags, { color: colors.onSurfaceSecondary }]}>
                {active.place.tags.length ? active.place.tags.join(" · ") : active.place.address ?? "Address unknown"}
              </Text>
            </View>
            <ParkingStatus parking={active.parking} />
            {options.length > 0 && (
              <Pressable testID="other-options-button" onPress={onShowOptions} hitSlop={8} style={styles.otherBtn}>
                <Text style={[styles.otherText, { color: colors.info }]}>
                  See {options.length} other option{options.length > 1 ? "s" : ""}
                </Text>
                <Icon name="chevron-right" size={18} color={colors.info} />
              </Pressable>
            )}
          </View>
        )}
      </Animated.View>

      <View style={styles.actions}>
        {(phase === "ready" || phase === "options") && <SideButton icon="bookmark-outline" label="Saved" testID="saved-button" />}
        {(phase === "listening" || phase === "thinking") && <SideButton icon="microphone-off" label="Mute" testID="mute-button" />}
        {navigating && <SideButton icon="volume-high" label="Speaker" testID="speaker-button" />}

        <Pressable
          testID="copilot-avatar-button"
          accessibilityRole="button"
          accessibilityLabel={caption}
          onPress={() => {
            haptic("medium");
            onAvatarPress();
          }}
          disabled={phase === "thinking"}
          style={styles.center}
        >
          <Avatar
            size={navigating || phase === "options" ? 80 : 112}
            mode={phase === "listening" ? "listening" : phase === "thinking" ? "thinking" : speaking ? "speaking" : "idle"}
            showMic={phase !== "thinking"}
          />
          <Text testID="avatar-caption" style={[styles.caption, { color: colors.onSurfaceSecondary }]}>
            {caption}
          </Text>
        </Pressable>

        {phase === "ready" && <SideButton icon="arrow-right-top" label="Route" testID="route-button" />}
        {phase === "options" && <SideButton icon="close" label="Close" testID="close-options-button" onPress={onClose} />}
        {(phase === "listening" || phase === "thinking") && (
          <SideButton icon="close" label="Close" testID="close-listening-button" onPress={onClose} />
        )}
        {navigating && <SideButton icon="close" label="End route" testID="end-route-button" onPress={onEndRoute} danger />}
      </View>

      {navigating && (
        <Animated.View entering={FadeInDown.duration(260)} style={styles.handoff}>
          <PressableScale
            testID="open-google-maps-button"
            onPress={onOpenGoogle}
            containerStyle={styles.handoffItem}
            style={[styles.handoffBtn, { backgroundColor: colors.brandPrimary }]}
          >
            <Icon name="google-maps" size={20} color={colors.onBrandPrimary} />
            <Text style={[styles.handoffText, { color: colors.onBrandPrimary }]}>Open in Google Maps</Text>
          </PressableScale>
          <PressableScale
            testID="open-waze-button"
            onPress={onOpenWaze}
            containerStyle={styles.handoffItem}
            style={[styles.handoffBtn, { backgroundColor: colors.surfaceSecondary }]}
          >
            <Icon name="waze" size={20} color={colors.onSurface} />
            <Text style={[styles.handoffText, { color: colors.onSurface }]}>Open in Waze</Text>
          </PressableScale>
        </Animated.View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  sheet: {
    position: "absolute",
    left: 0,
    right: 0,
    bottom: 0,
    borderTopLeftRadius: 28,
    borderTopRightRadius: 28,
    paddingTop: spacing.sm,
    paddingHorizontal: spacing.xl,
  },
  handle: { alignSelf: "center", width: 40, height: 5, borderRadius: 3, marginBottom: spacing.md },
  header: { minHeight: 36, justifyContent: "center" },
  title: { fontFamily: fonts.bold, fontSize: 22, textAlign: "center" },
  listenRow: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: spacing.md },
  navInfo: { gap: 2 },
  eta: { fontFamily: fonts.extrabold, fontSize: 26, letterSpacing: -0.3 },
  placeName: { fontFamily: fonts.semibold, fontSize: 17, marginTop: 2 },
  tagRow: { flexDirection: "row", alignItems: "center", gap: 6, marginTop: spacing.xs },
  tags: { fontFamily: fonts.medium, fontSize: 14, flexShrink: 1 },
  otherBtn: { flexDirection: "row", alignItems: "center", alignSelf: "flex-start", minHeight: 44, gap: 2 },
  otherText: { fontFamily: fonts.bold, fontSize: 14 },
  actions: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginTop: spacing.sm },
  side: { width: 72, alignItems: "center", gap: 6 },
  sideCircle: { width: 56, height: 56, borderRadius: 28, alignItems: "center", justifyContent: "center" },
  sideLabel: { fontFamily: fonts.semibold, fontSize: 13 },
  center: { alignItems: "center" },
  caption: { fontFamily: fonts.semibold, fontSize: 14, marginTop: 2 },
  handoff: { flexDirection: "row", gap: spacing.sm, marginTop: spacing.lg },
  handoffItem: { flex: 1 },
  handoffBtn: {
    height: 56,
    borderRadius: radius.md,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: spacing.sm,
  },
  handoffText: { fontFamily: fonts.bold, fontSize: 14 },
});
