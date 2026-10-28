import { useEffect, useState, type ReactNode } from "react";
import { Platform, Pressable, ScrollView, StyleSheet, Text, TextInput, useWindowDimensions, View } from "react-native";
import { GestureDetector, usePanGesture } from "react-native-gesture-handler";
import { KeyboardStickyView } from "@/src/keyboard";
import Animated, { FadeIn, FadeInDown, runOnJS, useAnimatedStyle, useSharedValue, withSpring } from "react-native-reanimated";

import { routesChipText } from "../geo/routeChoice";
import type { OfflineMapState } from "../hooks/useOfflineMap";
import { formatBytes } from "../map/bounds";
import { fonts, radius, shadow, spacing, useTheme } from "../theme";
import type { ActiveRoute, ParkingState, Phase, Place } from "../types";
import { Avatar } from "./Avatar";
import { Icon } from "./Icon";
import { OptionsList } from "./OptionsList";
import { ParkingLiveBadge } from "./ParkingLiveBadge";
import { ParkingStatus } from "./ParkingStatus";
import { haptic, PressableScale } from "./PressableScale";
import { ReviewsGist } from "./ReviewsGist";
import { Waveform } from "./Waveform";
import { entrySpot, routeEndsAt } from "../utils/entry";
import { dragProgress, EXPANDED_SHARE, heightAt, sheetHeights, snapTarget, type SheetHeights } from "../utils/sheetSnap";

/** Sheet height while nothing pins it: whatever the content needs (Yoga treats this as no cap). */
const AUTO_MAX_HEIGHT = 100000;
const SNAP_SPRING = { damping: 22, stiffness: 240, mass: 0.9, overshootClamping: true };

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
  /** Driving mode started with "Go": compact arrival info instead of the route card. */
  driving: boolean;
  remaining: { m: number; s: number } | null;
  onGo: () => void;
  bottomInset: number;
  onAvatarPress: () => void;
  onSubmitText: (text: string) => void;
  onClose: () => void;
  onEndRoute: () => void;
  /** Drop one via stop (the route is re-planned without it). */
  onRemoveStop?: (stop: Place) => void;
  /** Open the list of the other routes the engine offered. */
  onShowRoutes?: () => void;
  /** Offline corridor pack for this route (MapLibre builds only; null hides the button). */
  offlineMap?: OfflineMapState | null;
  onDownloadMap?: () => void;
  onLayout: (h: number) => void;
  /** Set while the wake word is actively listening (shows the privacy dot + phrase). */
  wakePhrase?: string | null;
  /** Breakdown results (phase "providers"). */
  panel?: ReactNode;
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

function OfflineMapButton({ state, onPress }: { state: OfflineMapState; onPress?: () => void }) {
  const { colors } = useTheme();
  const busy = state.kind === "downloading";
  const saved = state.kind === "saved";
  return (
    <Pressable
      testID="offline-map-button"
      accessibilityLabel={offlineMapLabel(state)}
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

function arrivalClock(seconds: number) {
  const d = new Date(Date.now() + seconds * 1000);
  return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function durationLabel(seconds: number) {
  const min = Math.max(1, Math.round(seconds / 60));
  if (min < 60) return `${min} min`;
  const h = Math.floor(min / 60);
  const m = min % 60;
  return m ? `${h} h ${m} min` : `${h} h`;
}

export function CopilotSheet(props: Props) {
  const { phase, speaking, onShowOptions, thinkingText, options, parking, onOptionParking, onOptionRoute, active, driving, remaining, onGo, bottomInset, onAvatarPress, onSubmitText, onClose, onEndRoute, onRemoveStop, onShowRoutes, offlineMap, onDownloadMap, onLayout, wakePhrase, panel } = props;
  const routesChip = active && !driving ? routesChipText(active.route) : null;
  const { colors } = useTheme();
  const navigating = phase === "navigating";
  const canType = phase === "ready" || phase === "navigating" || phase === "options" || phase === "providers";
  const [typing, setTyping] = useState(false);
  const [draft, setDraft] = useState("");

  // Drag: the sheet rests at its content height (progress 0) or at EXPANDED_SHARE of the window
  // (progress 1). While nothing pins it the height is auto, exactly as before; from the first
  // drag frame on it is pinned to the interpolated height so the body can shrink and scroll.
  const { height: windowH } = useWindowDimensions();
  const expandedH = Math.round(windowH * EXPANDED_SHARE);
  const progress = useSharedValue(0);
  const collapsedH = useSharedValue(0); // last height measured while resting collapsed
  const dragStart = useSharedValue(0);
  // Remembered with the phase it was reached in: a new phase swaps the content, so the tall
  // state (sized for the old one) does not carry over.
  const [tall, setTall] = useState<{ phase: Phase; on: boolean }>({ phase, on: false });
  const expanded = tall.on && tall.phase === phase;
  const markExpanded = (on: boolean) => setTall({ phase, on });
  // The options and providers lists scroll on their own; a pan over them would steal that scroll.
  const bodyDraggable = !expanded && phase !== "options" && phase !== "providers";

  const heights = (): SheetHeights => {
    "worklet";
    return sheetHeights(collapsedH.get(), expandedH);
  };
  const panConfig = {
    activeOffsetY: [-8, 8] as [number, number],
    failOffsetX: [-24, 24] as [number, number],
    onActivate: () => {
      "worklet";
      dragStart.set(progress.get());
    },
    onUpdate: (e: { translationY: number }) => {
      "worklet";
      progress.set(dragProgress(dragStart.get(), e.translationY, heights()));
    },
    onDeactivate: (e: { velocityY: number }) => {
      "worklet";
      const h = heights();
      const target = snapTarget(heightAt(progress.get(), h), e.velocityY, h);
      progress.set(withSpring(target === "expanded" ? 1 : 0, SNAP_SPRING));
      runOnJS(markExpanded)(target === "expanded");
    },
  };
  const handlePan = usePanGesture(panConfig);
  const bodyPan = usePanGesture({ ...panConfig, enabled: bodyDraggable });

  const sheetStyle = useAnimatedStyle(() => {
    const p = progress.get();
    const base = collapsedH.get();
    if (p <= 0 || base <= 0) return { minHeight: 0, maxHeight: AUTO_MAX_HEIGHT };
    const h = heightAt(p, sheetHeights(base, expandedH));
    return { minHeight: h, maxHeight: h };
  });

  // The pinned height goes with the content it was sized for.
  useEffect(() => {
    progress.set(0);
  }, [phase, progress]);

  const onSheetLayout = (h: number) => {
    // Only the resting, collapsed height is the map's bottom inset; the expanded sheet may cover
    // the map, the camera does not follow it (that would refit the options on every drag frame).
    if (progress.get() !== 0) return;
    collapsedH.set(h);
    onLayout(h);
  };

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
    <Animated.View
      testID="copilot-sheet"
      onLayout={(e) => onSheetLayout(e.nativeEvent.layout.height)}
      style={[styles.sheet, shadow(colors, 2), sheetStyle, { backgroundColor: colors.surface, paddingBottom: bottomInset + spacing.lg }]}
    >
      <GestureDetector gesture={handlePan}>
        <View testID="sheet-grabber" accessibilityRole="adjustable" accessibilityLabel={expanded ? "Collapse" : "Expand"} hitSlop={{ top: 12, bottom: 4 }} style={styles.grab}>
          <View style={[styles.handle, { backgroundColor: colors.surfaceTertiary }]} />
        </View>
      </GestureDetector>

      <GestureDetector gesture={bodyPan}>
      <ScrollView
        testID="sheet-body"
        style={styles.body}
        scrollEnabled={expanded}
        bounces={expanded}
        showsVerticalScrollIndicator={false}
        keyboardShouldPersistTaps="handled"
      >
      <Animated.View key={`head-${phase}`} entering={FadeIn.duration(220)} style={styles.header}>
        {phase === "ready" && (
          <Text testID="sheet-title" style={[styles.title, { color: colors.onSurface }]}>
            Ask your copilot
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
        {phase === "providers" && panel}
        {phase === "options" && (
          <OptionsList options={options} parking={parking} onParking={onOptionParking} onRoute={onOptionRoute} />
        )}
        {navigating && active && driving && remaining && (
          <View testID="driving-info" style={styles.driveInfo}>
            <Text testID="arrival-time" style={[styles.eta, { color: colors.success }]}>
              {arrivalClock(remaining.s)}
            </Text>
            <Text testID="remaining-info" numberOfLines={1} style={[styles.tags, { color: colors.onSurfaceSecondary }]}>
              {durationLabel(remaining.s)} · {(remaining.m / 1609.34).toFixed(remaining.m < 16093 ? 1 : 0)} mi · {active.place.name}
            </Text>
          </View>
        )}
        {navigating && active && !driving && (
          <View style={styles.navInfo}>
            <Animated.Text key={`sum-${active.route.summary}`} entering={FadeInDown.springify().damping(15)} testID="route-summary" style={[styles.eta, { color: colors.onSurface }]}>
              {active.route.summary}
            </Animated.Text>
            {(() => {
              const e = entrySpot(active.parking);
              return e && routeEndsAt(active.route, e) ? (
                <Animated.View key={`to-entry-${active.place.id}`} entering={FadeIn} style={styles.entryRow}>
                  <Icon name="login-variant" size={16} color={colors.info} />
                  <Text testID="route-to-entry" style={[styles.notice, { color: colors.info }]}>
                    Route ends at truck entrance
                  </Text>
                </Animated.View>
              ) : null;
            })()}
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
            <Animated.Text key={`name-${active.place.id}`} entering={FadeInDown.delay(70).springify().damping(15)} testID="route-place-name" numberOfLines={1} style={[styles.placeName, { color: colors.onSurface }]}>
              {active.place.name}
            </Animated.Text>
            <View style={styles.tagRow}>
              <Icon name={active.place.tags.length ? "truck" : "map-marker-outline"} size={18} color={colors.onSurfaceSecondary} />
              <Text testID="route-place-tags" numberOfLines={1} style={[styles.tags, { color: colors.onSurfaceSecondary }]}>
                {active.place.tags.length ? active.place.tags.join(" · ") : active.place.address ?? (active.place.category === "destination" ? "Destination" : "Address unknown")}
              </Text>
            </View>
            {!!active.stops?.length && (
              <View testID="stops-row" style={styles.stopsRow}>
                <Text style={[styles.stopsLabel, { color: colors.onSurfaceSecondary }]}>Stops</Text>
                {active.stops.map((s) => (
                  <View key={s.id} testID={`stop-${s.id}`} style={[styles.stopChip, { backgroundColor: colors.surfaceSecondary }]}>
                    <Text numberOfLines={1} style={[styles.stopName, { color: colors.onSurface }]}>
                      {s.name}
                    </Text>
                    <Pressable testID={`stop-remove-${s.id}`} accessibilityLabel={`Remove stop ${s.name}`} hitSlop={10} onPress={() => onRemoveStop?.(s)}>
                      <Icon name="close" size={16} color={colors.onSurfaceSecondary} />
                    </Pressable>
                  </View>
                ))}
              </View>
            )}
            {!!active.place.reviews?.length && <ReviewsGist reviews={active.place.reviews} />}
            <ParkingLiveBadge place={active.place} />
            {active.place.category !== "service" && <ParkingStatus parking={active.parking} place={active.place} route={active.route.geometry} />}
            {(options.length > 0 || routesChip) && (
              <View style={styles.linkRow}>
                {routesChip && (
                  <Pressable testID="routes-chip" accessibilityLabel="Other routes" onPress={onShowRoutes} hitSlop={8} style={[styles.routesChip, { borderColor: colors.info }]}>
                    <Icon name="directions-fork" size={16} color={colors.info} />
                    <Text style={[styles.otherText, { color: colors.info }]}>{routesChip}</Text>
                  </Pressable>
                )}
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
            {offlineMap && <OfflineMapButton state={offlineMap} onPress={onDownloadMap} />}
            <PressableScale
              testID="go-button"
              accessibilityLabel="Go"
              onPress={onGo}
              haptic="heavy"
              style={[styles.goBtn, { backgroundColor: colors.info }]}
            >
              <Icon name="navigation-variant" size={22} color={colors.onInfo} />
              <Text style={[styles.goText, { color: colors.onInfo }]}>Go</Text>
            </PressableScale>
          </View>
        )}
      </Animated.View>
      </ScrollView>
      </GestureDetector>

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
            size={navigating || phase === "options" || phase === "providers" ? 72 : 104}
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
        {(phase === "options" || phase === "providers") && <SideButton icon="close" label="Close" testID="close-options-button" onPress={onClose} />}
        {(phase === "listening" || phase === "thinking") && (
          <SideButton icon="close" label="Close" testID="close-listening-button" onPress={onClose} />
        )}
        {navigating && <SideButton icon="close" label="End route" testID="end-route-button" onPress={onEndRoute} danger />}
      </View>
      )}
    </Animated.View>
    </KeyboardStickyView>
  );
}

const styles = StyleSheet.create({
  wrap: { position: "absolute", left: 0, right: 0, bottom: 0 },
  driveInfo: { alignItems: "center", gap: 2 },
  goBtn: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: spacing.sm, height: 52, borderRadius: 26, marginTop: spacing.sm },
  goText: { fontFamily: fonts.bold, fontSize: 18 },
  inputRow: { flexDirection: "row", alignItems: "center", gap: spacing.sm, marginTop: spacing.md },
  input: { flex: 1, minWidth: 0, width: 0, minHeight: 52, borderRadius: 26, paddingHorizontal: spacing.lg, fontFamily: fonts.medium, fontSize: 16, ...(Platform.OS === "web" ? ({ outlineStyle: "none" } as object) : {}) },
  roundBtn: { flexShrink: 0, width: 52, height: 52, borderRadius: 26, alignItems: "center", justifyContent: "center" },
  sheet: {
    borderTopLeftRadius: 28,
    borderTopRightRadius: 28,
    paddingTop: spacing.sm,
    paddingHorizontal: spacing.xl,
  },
  grab: { alignItems: "center", paddingBottom: spacing.md },
  handle: { width: 40, height: 5, borderRadius: 3 },
  // Shrinks (and scrolls) once the sheet is pinned to a height; sized by its content otherwise.
  body: { flexGrow: 1, flexShrink: 1 },
  header: { minHeight: 36, justifyContent: "center" },
  title: { fontFamily: fonts.bold, fontSize: 22, textAlign: "center" },
  listenRow: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: spacing.md },
  navInfo: { gap: 2 },
  eta: { fontFamily: fonts.extrabold, fontSize: 24, letterSpacing: -0.3 },
  placeName: { fontFamily: fonts.semibold, fontSize: 16, marginTop: 4 },
  tagRow: { flexDirection: "row", alignItems: "center", gap: 6, marginTop: spacing.xs },
  tags: { fontFamily: fonts.medium, fontSize: 14, flexShrink: 1 },
  notice: { fontFamily: fonts.medium, fontSize: 12 },
  entryRow: { flexDirection: "row", alignItems: "center", gap: 4 },
  stopsRow: { flexDirection: "row", flexWrap: "wrap", alignItems: "center", gap: 6, marginTop: spacing.xs },
  stopsLabel: { fontFamily: fonts.medium, fontSize: 13 },
  stopChip: { flexDirection: "row", alignItems: "center", gap: 4, paddingLeft: spacing.sm, paddingRight: 6, height: 28, borderRadius: radius.pill, maxWidth: 200 },
  stopName: { fontFamily: fonts.semibold, fontSize: 13, flexShrink: 1 },
  linkRow: { flexDirection: "row", alignItems: "center", gap: spacing.md },
  routesChip: { flexDirection: "row", alignItems: "center", gap: 4, height: 32, paddingHorizontal: spacing.sm, borderRadius: radius.pill, borderWidth: 1 },
  otherBtn: { flexDirection: "row", alignItems: "center", alignSelf: "flex-start", minHeight: 44, gap: 2 },
  offlineBtn: { flexDirection: "row", flexWrap: "wrap", alignItems: "center", alignSelf: "flex-start", gap: 6, minHeight: 32, paddingHorizontal: spacing.sm, paddingVertical: 4, borderRadius: radius.pill, borderWidth: 1, marginTop: spacing.xs },
  progressTrack: { width: "100%", height: 3, borderRadius: 2, overflow: "hidden" },
  progressFill: { height: 3 },
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
