import { useEffect, useState, type ReactNode } from "react";
import { Keyboard, Platform, Pressable, StyleSheet, Text, TextInput, useWindowDimensions, View, type LayoutChangeEvent } from "react-native";
import { GestureDetector, usePanGesture } from "react-native-gesture-handler";
import { KeyboardStickyView } from "@/src/keyboard";
import Animated, { runOnJS, useAnimatedStyle, useSharedValue, withSpring } from "react-native-reanimated";

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
import { dragHeight, sheetHeights, snapTarget, stateHeight, type SheetHeights, type SnapState } from "../utils/sheetSnap";

/** Sheet height while nothing pins it: whatever the content needs (Yoga treats this as no cap). */
const AUTO_MAX_HEIGHT = 100000;
/** Body cap in auto, so a long list or route card leaves map above the sheet; the rest is `full`. */
const AUTO_BODY_SHARE = 0.5;
const SNAP_SPRING = { damping: 22, stiffness: 240, mass: 0.9, overshootClamping: true };
/** Vertical travel before the drag takes over; sideways travel that hands the touch to a chip row. */
const PAN_ACTIVE_Y: [number, number] = [-8, 8];
const PAN_FAIL_X: [number, number] = [-24, 24];

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
  /** The copilot's last answer, shown under the title while idle (the caption on the map fades). */
  lastReply?: string | null;
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
  /** The sheet's height while peeked (map chrome may move down into the room), null otherwise. */
  onPeekHeight?: (h: number | null) => void;
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
  const { phase, speaking, onShowOptions, thinkingText, options, parking, onOptionParking, onOptionRoute, active, driving, remaining, onGo, bottomInset, onAvatarPress, onSubmitText, lastReply, onClose, onEndRoute, onRemoveStop, onShowRoutes, offlineMap, onDownloadMap, onLayout, onPeekHeight, wakePhrase, panel } = props;
  const routesChip = active && !driving ? routesChipText(active.route) : null;
  const { colors } = useTheme();
  const navigating = phase === "navigating";
  const canType = phase === "ready" || phase === "navigating" || phase === "options" || phase === "providers";
  const [typing, setTyping] = useState(false);
  const [draft, setDraft] = useState("");

  // Drag: three resting heights, all derived from the content (src/utils/sheetSnap.ts): peek
  // (grabber + first header line + controls), auto (the content's own height, where every phase
  // starts) and full (only when auto clips the body). While the sheet rests in auto nothing pins
  // it and the height is the content's, exactly as before; from the first drag frame on it is
  // pinned (`pinnedH`), so the body shrinks (peek) or grows and scrolls (full). Everything the
  // geometry needs is measured with onLayout while resting in auto.
  const { height: windowH } = useWindowDimensions();
  const pinnedH = useSharedValue(0); // 0 = resting in auto, unpinned
  const autoH = useSharedValue(0);
  const bodyVisibleH = useSharedValue(0);
  const bodyContentH = useSharedValue(0);
  const firstLineBottom = useSharedValue(0);
  const dragStart = useSharedValue(0);
  const [bodyMeasure, setBodyMeasure] = useState({ visible: 0, content: 0 });
  const clipped = bodyMeasure.content > bodyMeasure.visible + 1;
  // Remembered with the phase it was reached in: a new phase swaps the content and starts in auto.
  const [snap, setSnap] = useState<{ phase: Phase; state: SnapState }>({ phase, state: "auto" });
  const state: SnapState = snap.phase === phase ? snap.state : "auto";
  // A clipped body scrolls on its own; a pan over it would steal that scroll (the grabber still drags).
  const bodyDraggable = !clipped;

  const heights = (): SheetHeights => {
    "worklet";
    const a = autoH.get();
    const visible = bodyVisibleH.get();
    const hidden = Math.max(0, visible - firstLineBottom.get());
    return sheetHeights({ peek: a - hidden, auto: a, content: a - visible + bodyContentH.get(), window: windowH });
  };
  const markSnap = (s: SnapState, peekH: number) => {
    setSnap({ phase, state: s });
    onPeekHeight?.(s === "peek" ? peekH : null);
  };
  const settle = (target: SnapState, h: SheetHeights) => {
    "worklet";
    pinnedH.set(
      withSpring(stateHeight(target, h), SNAP_SPRING, (finished) => {
        "worklet";
        if (finished && target === "auto") pinnedH.set(0); // back to auto: let the content size it again
      }),
    );
    runOnJS(markSnap)(target, h.peek);
  };
  const onActivate = () => {
    "worklet";
    dragStart.set(pinnedH.get() || autoH.get());
  };
  const onUpdate = (e: { translationY: number }) => {
    "worklet";
    pinnedH.set(dragHeight(dragStart.get(), e.translationY, heights()));
  };
  const onDeactivate = (e: { velocityY: number }) => {
    "worklet";
    const h = heights();
    settle(snapTarget(pinnedH.get(), e.velocityY, h, state), h);
  };
  // Two detectors, one behaviour (a gesture object cannot be attached twice). The worklets babel
  // plugin wants object literals here, so the config is spelled out for each.
  const handlePan = usePanGesture({ activeOffsetY: PAN_ACTIVE_Y, failOffsetX: PAN_FAIL_X, onActivate, onUpdate, onDeactivate });
  const bodyPan = usePanGesture({ activeOffsetY: PAN_ACTIVE_Y, failOffsetX: PAN_FAIL_X, enabled: bodyDraggable, onActivate, onUpdate, onDeactivate });

  /** One tap on the grabber: peek <-> auto (a one-handed collapse in the cab). */
  const toggleGrabber = () => {
    const h = heights();
    const target: SnapState = state === "peek" ? "auto" : "peek";
    if (target === "peek" && h.peek >= h.auto) return; // nothing to hide
    haptic("light");
    settle(target, h);
  };

  const sheetStyle = useAnimatedStyle(() => {
    const p = pinnedH.get();
    return p > 0 ? { minHeight: p, maxHeight: p } : { minHeight: 0, maxHeight: AUTO_MAX_HEIGHT };
  });
  // In auto the body is capped (this is what used to be the lists' own maxHeight); pinned, the
  // sheet's height bounds it instead.
  const bodyStyle = useAnimatedStyle(() => ({ maxHeight: pinnedH.get() > 0 ? AUTO_MAX_HEIGHT : windowH * AUTO_BODY_SHARE }));

  // A new phase swaps the content: unpin, forget the old first line, and the map gets its inset back.
  useEffect(() => {
    pinnedH.set(0);
    firstLineBottom.set(0);
    onPeekHeight?.(null);
  }, [phase, pinnedH, firstLineBottom, onPeekHeight]);

  const resting = () => pinnedH.get() === 0;
  const onSheetLayout = (h: number) => {
    // Only the auto height is the map's bottom inset; peek/full do not move the camera (that
    // would refit the options on every drag frame). The screen gets peek separately.
    if (!resting()) return;
    autoH.set(h);
    onLayout(h);
  };
  const onBodyLayout = (h: number) => {
    if (!resting()) return;
    bodyVisibleH.set(h);
    setBodyMeasure((m) => (m.visible === h ? m : { ...m, visible: h }));
  };
  const onBodyContent = (h: number) => {
    bodyContentH.set(h);
    setBodyMeasure((m) => (m.content === h ? m : { ...m, content: h }));
  };
  /** The header line that stays visible in peek; `y` is relative to the header. */
  const onFirstLine = (e: LayoutChangeEvent) => firstLineBottom.set(e.nativeEvent.layout.y + e.nativeEvent.layout.height);

  const send = () => {
    const text = draft.trim();
    if (!text) return;
    haptic("medium");
    Keyboard.dismiss(); // before the input unmounts, or Android hands the focus to "Where to?" and keeps the keyboard up
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
        <Pressable
          testID="sheet-grabber"
          accessibilityRole="button"
          accessibilityLabel={state === "peek" ? "Show details" : "Hide details"}
          onPress={toggleGrabber}
          hitSlop={{ top: 12, bottom: 4 }}
          style={styles.grab}
        >
          <View style={[styles.handle, { backgroundColor: colors.surfaceTertiary }]} />
        </Pressable>
      </GestureDetector>

      <GestureDetector gesture={bodyPan}>
      <Animated.ScrollView
        testID="sheet-body"
        style={[styles.body, bodyStyle]}
        onLayout={(e) => onBodyLayout(e.nativeEvent.layout.height)}
        onContentSizeChange={(_w, h) => onBodyContent(h)}
        scrollEnabled={clipped && state !== "peek"}
        bounces={clipped}
        showsVerticalScrollIndicator={false}
        keyboardShouldPersistTaps="handled"
      >
      <View style={styles.header}>
        {phase === "ready" && (
          <View onLayout={onFirstLine}>
            <Text testID="sheet-title" style={[styles.title, { color: colors.onSurface }]}>
              Ask your copilot
            </Text>
            {!!lastReply && (
              <Text testID="sheet-last-reply" numberOfLines={3} style={[styles.lastReply, { color: colors.onSurfaceSecondary }]}>
                {lastReply}
              </Text>
            )}
          </View>
        )}
        {phase === "listening" && (
          <View onLayout={onFirstLine} style={styles.listenRow}>
            <Text testID="sheet-title" style={[styles.title, { color: colors.onSurface }]}>
              I&apos;m listening
            </Text>
            <Waveform />
          </View>
        )}
        {phase === "thinking" && (
          <View onLayout={onFirstLine}>
            <Text testID="sheet-title" style={[styles.title, { color: colors.onSurface }]}>
              {thinkingText ?? "Finding the best spot…"}
            </Text>
          </View>
        )}
        {phase === "providers" && panel}
        {phase === "options" && (
          <OptionsList options={options} parking={parking} onParking={onOptionParking} onRoute={onOptionRoute} onTitleLayout={onFirstLine} />
        )}
        {navigating && active && driving && remaining && (
          <View testID="driving-info" style={styles.driveInfo}>
            <View onLayout={onFirstLine}>
              <Text testID="arrival-time" style={[styles.eta, { color: colors.success }]}>
                {arrivalClock(remaining.s)}
              </Text>
            </View>
            <Text testID="remaining-info" numberOfLines={1} style={[styles.tags, { color: colors.onSurfaceSecondary }]}>
              {durationLabel(remaining.s)} · {(remaining.m / 1609.34).toFixed(remaining.m < 16093 ? 1 : 0)} mi · {active.place.name}
            </Text>
          </View>
        )}
        {navigating && active && !driving && (
          <View style={styles.navInfo}>
            <View onLayout={onFirstLine}>
              <Text testID="route-summary" style={[styles.eta, { color: colors.onSurface }]}>
                {active.route.summary}
              </Text>
            </View>
            {(() => {
              const e = entrySpot(active.parking);
              return e && routeEndsAt(active.route, e) ? (
                <View style={styles.entryRow}>
                  <Icon name="login-variant" size={16} color={colors.info} />
                  <Text testID="route-to-entry" style={[styles.notice, { color: colors.info }]}>
                    Route ends at truck entrance
                  </Text>
                </View>
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
            <Text testID="route-place-name" numberOfLines={1} style={[styles.placeName, { color: colors.onSurface }]}>
              {active.place.name}
            </Text>
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
      </View>
      </Animated.ScrollView>
      </GestureDetector>

      {typing && canType ? (
        <View style={styles.inputRow}>
          <TextInput
            testID="copilot-text-input"
            value={draft}
            onChangeText={setDraft}
            onSubmitEditing={send}
            // The keyboard went away (map tap, swipe, Done): an empty field has nothing to keep open.
            onBlur={() => {
              if (!draft.trim()) setTyping(false);
            }}
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
            testID="copilot-text-close"
            accessibilityLabel="Done typing"
            onPress={() => {
              Keyboard.dismiss();
              setTyping(false);
            }}
            style={[styles.roundBtn, { backgroundColor: colors.surfaceSecondary }]}
          >
            <Icon name="close" size={22} color={colors.onSurface} />
          </PressableScale>
        </View>
      ) : (
      <View style={styles.actions}>
        {canType ? (
          <SideButton icon="keyboard-outline" label="Type" testID="type-button" onPress={() => setTyping((t) => !t)} />
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
  // Shrinks (peek) or grows and scrolls (full) once the sheet is pinned; sized by its content otherwise.
  body: { flexGrow: 1, flexShrink: 1 },
  header: { minHeight: 36, justifyContent: "center" },
  title: { fontFamily: fonts.bold, fontSize: 22, textAlign: "center" },
  lastReply: { fontFamily: fonts.regular, fontSize: 15, lineHeight: 21, textAlign: "center", marginTop: spacing.xs },
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
