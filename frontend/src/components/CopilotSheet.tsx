import { useState } from "react";
import { Pressable, StyleSheet, Text, TextInput, View } from "react-native";
import { KeyboardStickyView } from "react-native-keyboard-controller";
import Animated, { FadeIn } from "react-native-reanimated";

import { fonts, shadow, spacing, useTheme } from "../theme";
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
  onSubmitText: (text: string) => void;
  onClose: () => void;
  onEndRoute: () => void;
  onLayout: (h: number) => void;
  /** Set while the wake word is actively listening (shows the privacy dot + phrase). */
  wakePhrase?: string | null;
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
      <View style={[styles.sideCircle, { backgroundColor: colors.surfaceSecondary }]}>
        <Icon name={icon} size={24} color={danger ? colors.error : colors.onSurface} />
      </View>
      <Text style={[styles.sideLabel, { color: danger ? colors.error : colors.onSurfaceSecondary }]}>{label}</Text>
    </PressableScale>
  );
}

export function CopilotSheet(props: Props) {
  const { phase, speaking, onShowOptions, thinkingText, options, parking, onOptionParking, onOptionRoute, active, bottomInset, onAvatarPress, onSubmitText, onClose, onEndRoute, onLayout, wakePhrase } = props;
  const { colors } = useTheme();
  const navigating = phase === "navigating";
  const canType = phase === "ready" || phase === "navigating" || phase === "options";
  const [typing, setTyping] = useState(false);
  const [draft, setDraft] = useState("");
  const send = () => {
    const text = draft.trim();
    if (!text) return;
    haptic("medium");
    setDraft("");
    setTyping(false);
    onSubmitText(text);
  };

  const caption =
    phase === "listening"
      ? "Tap to stop"
      : phase === "thinking"
        ? "Thinking…"
        : speaking
          ? "Speaking…"
          : wakePhrase
            ? `Say "${wakePhrase}"`
            : navigating
              ? "Talk to copilot"
              : "Tap to talk";

  return (
    <KeyboardStickyView offset={{ closed: 0, opened: bottomInset }} style={styles.wrap}>
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
              {active.route.summary}
            </Text>
            {active.route.truck_aware === false && (
              <Text testID="route-not-truck-checked" style={[styles.notice, { color: colors.warning }]}>
                Estimated route · truck restrictions not checked
              </Text>
            )}
            {!!active.route.notices?.length && (
              <Text testID="route-notices" numberOfLines={2} style={[styles.notice, { color: colors.error }]}>
                {active.route.notices.slice(0, 2).join(" · ")}
              </Text>
            )}
            <Text testID="route-place-name" numberOfLines={1} style={[styles.placeName, { color: colors.onSurface }]}>
              {active.place.name}
            </Text>
            <View style={styles.tagRow}>
              <Icon name={active.place.tags.length ? "truck" : "map-marker-outline"} size={18} color={colors.onSurfaceSecondary} />
              <Text testID="route-place-tags" numberOfLines={1} style={[styles.tags, { color: colors.onSurfaceSecondary }]}>
                {active.place.tags.length ? active.place.tags.join(" · ") : active.place.address ?? (active.place.category === "destination" ? "Destination" : "Address unknown")}
              </Text>
            </View>
            {active.place.category !== "destination" && <ParkingStatus parking={active.parking} />}
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

      {typing && canType ? (
        <Animated.View entering={FadeIn.duration(180)} style={styles.inputRow}>
          <TextInput
            testID="copilot-text-input"
            value={draft}
            onChangeText={setDraft}
            onSubmitEditing={send}
            placeholder="Type a request…"
            placeholderTextColor={colors.onSurfaceSecondary}
            returnKeyType="send"
            autoFocus
            style={[styles.input, { backgroundColor: colors.surfaceSecondary, color: colors.onSurface }]}
          />
          <PressableScale
            testID="copilot-send-button"
            accessibilityLabel="Send"
            onPress={send}
            disabled={!draft.trim()}
            style={[styles.roundBtn, { backgroundColor: draft.trim() ? colors.info : colors.surfaceTertiary }]}
          >
            <Icon name="send" size={22} color={draft.trim() ? colors.onInfo : colors.onSurfaceSecondary} />
          </PressableScale>
          <PressableScale
            testID="copilot-text-cancel"
            accessibilityLabel="Cancel typing"
            onPress={() => setTyping(false)}
            style={[styles.roundBtn, { backgroundColor: colors.surfaceSecondary }]}
          >
            <Icon name="microphone" size={22} color={colors.onSurface} />
          </PressableScale>
        </Animated.View>
      ) : (
      <View style={styles.actions}>
        {canType ? (
          <SideButton icon="keyboard-outline" label="Type" testID="type-button" onPress={() => setTyping(true)} />
        ) : (
          <View style={styles.side} />
        )}

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
            size={navigating || phase === "options" ? 72 : 104}
            mode={phase === "listening" ? "listening" : phase === "thinking" ? "thinking" : speaking ? "speaking" : "idle"}
            showMic={phase !== "thinking"}
          />
          <View style={styles.captionRow}>
            {!!wakePhrase && (
              <View testID="wake-indicator" accessibilityLabel="Listening for wake word" style={[styles.wakeDot, { backgroundColor: colors.success }]} />
            )}
            <Text testID="avatar-caption" style={[styles.caption, { color: colors.onSurfaceSecondary }]}>
              {caption}
            </Text>
          </View>
        </Pressable>

        {phase === "ready" && <View style={styles.side} />}
        {phase === "options" && <SideButton icon="close" label="Close" testID="close-options-button" onPress={onClose} />}
        {(phase === "listening" || phase === "thinking") && (
          <SideButton icon="close" label="Close" testID="close-listening-button" onPress={onClose} />
        )}
        {navigating && <SideButton icon="close" label="End route" testID="end-route-button" onPress={onEndRoute} danger />}
      </View>
      )}
    </View>
    </KeyboardStickyView>
  );
}

const styles = StyleSheet.create({
  wrap: { position: "absolute", left: 0, right: 0, bottom: 0 },
  inputRow: { flexDirection: "row", alignItems: "center", gap: spacing.sm, marginTop: spacing.md },
  input: { flex: 1, minWidth: 0, width: 0, minHeight: 52, borderRadius: 26, paddingHorizontal: spacing.lg, fontFamily: fonts.medium, fontSize: 16 },
  roundBtn: { flexShrink: 0, width: 52, height: 52, borderRadius: 26, alignItems: "center", justifyContent: "center" },
  sheet: {
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
  eta: { fontFamily: fonts.extrabold, fontSize: 24, letterSpacing: -0.3 },
  placeName: { fontFamily: fonts.semibold, fontSize: 16, marginTop: 4 },
  tagRow: { flexDirection: "row", alignItems: "center", gap: 6, marginTop: spacing.xs },
  tags: { fontFamily: fonts.medium, fontSize: 14, flexShrink: 1 },
  notice: { fontFamily: fonts.medium, fontSize: 12 },
  otherBtn: { flexDirection: "row", alignItems: "center", alignSelf: "flex-start", minHeight: 44, gap: 2 },
  otherText: { fontFamily: fonts.bold, fontSize: 14 },
  actions: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginTop: spacing.sm },
  side: { width: 72, alignItems: "center", gap: 6 },
  sideCircle: { width: 56, height: 56, borderRadius: 28, alignItems: "center", justifyContent: "center" },
  sideLabel: { fontFamily: fonts.medium, fontSize: 12 },
  center: { alignItems: "center" },
  captionRow: { flexDirection: "row", alignItems: "center", gap: 6, marginTop: 4 },
  caption: { fontFamily: fonts.medium, fontSize: 13 },
  wakeDot: { width: 8, height: 8, borderRadius: 4 },
});
