import { useEffect, useState } from "react";
import { PixelRatio, Pressable, ScrollView, StyleSheet, View } from "react-native";
import { Text } from "../i18n/Text";
import Animated, { FadeInUp, FadeOutUp } from "react-native-reanimated";

import { fitBubble, REPLY_LINE } from "../dialog/caption";
import { fonts, radius, shadow, spacing, useTheme } from "../theme";
import type { MapsSource } from "../types";
import { Icon } from "./Icon";
import { MapsSources } from "./MapsSources";

export type ToastData = { id: number; text: string; kind: "error" | "info" };

export function Toast({ toast, top }: { toast: ToastData | null; top: number }) {
  const { colors } = useTheme();
  if (!toast) return null;
  return (
    <Animated.View
      key={toast.id}
      entering={FadeInUp.duration(220)}
      exiting={FadeOutUp.duration(180)}
      style={[styles.wrap, { top }]}
      pointerEvents="none"
    >
      <View testID="toast-message" style={[styles.toast, shadow(colors, 2), { backgroundColor: colors.surfaceInverse }]}>
        <Icon
          name={toast.kind === "error" ? "alert-circle-outline" : "information-outline"}
          size={20}
          color={toast.kind === "error" ? colors.error : colors.onSurfaceInverse}
        />
        <Text style={[styles.text, { color: colors.onSurfaceInverse }]}>{toast.text}</Text>
      </View>
    </Animated.View>
  );
}

/** The copilot's words over the map, until the driver closes them or a new answer replaces them
 *  (src/dialog/caption.ts). A spoken line that came after the answer shows under it as `note`. When the whole
 *  answer does not fit in `maxHeight`, `onFit(false)` tells the screen to move it into the sheet; where the sheet
 *  has no place for it, the answer scrolls in an area cut at a line boundary (fitBubble), never mid-letter. */
export function CaptionBubble({
  text,
  heard,
  note,
  bottom,
  maxHeight,
  sources,
  onDismiss,
  onHeight,
  onFit,
}: {
  text: string | null;
  heard?: string;
  note?: string;
  bottom: number;
  /** Room the bubble may take on the map; a longer answer scrolls inside it. */
  maxHeight?: number;
  sources?: MapsSource[];
  onDismiss?: () => void;
  /** The bubble's drawn height (0 once it is gone), so the map can keep the destination out from under it. */
  onHeight?: (h: number) => void;
  /** Whether the whole answer (with "You said" and the note) fits in maxHeight, once it has been measured. */
  onFit?: (fits: boolean) => void;
}) {
  const { colors } = useTheme();
  useEffect(() => {
    if (!text) onHeight?.(0);
  }, [text, onHeight]);
  const [textY, setTextY] = useState<number | null>(null);
  const [contentH, setContentH] = useState<number | null>(null);
  const [belowH, setBelowH] = useState<number | null>(null);
  const [line, setLine] = useState(REPLY_LINE * PixelRatio.getFontScale());
  // A new answer remounts the bubble (key={text}) and is measured afresh: the last answer's sizes must not
  // decide whether this one fits.
  const [measuredFor, setMeasuredFor] = useState(text);
  if (measuredFor !== text) {
    setMeasuredFor(text);
    setTextY(null);
    setContentH(null);
  }
  const fit =
    text && maxHeight !== undefined && textY !== null && contentH !== null && belowH !== null
      ? fitBubble({ room: maxHeight, textY, contentH, belowH, line })
      : null;
  const fits = fit?.fits;
  useEffect(() => {
    if (fits !== undefined) onFit?.(fits);
  }, [fits, onFit]);
  if (!text) return null;
  return (
    <Animated.View
      key={text}
      entering={FadeInUp.duration(260)}
      exiting={FadeOutUp.duration(180)}
      style={[styles.captionWrap, { bottom }]}
      pointerEvents="box-none"
    >
      {/* A column with the scrolling answer on top and the note under it: the note never scrolls, so the
          bubble's edge cannot cut it through the letters (UI review 2026-11-05, N4, ui2-speaking-dark). */}
      <View
        testID="agent-reply-caption"
        onLayout={(e) => onHeight?.(e.nativeEvent.layout.height)}
        style={[styles.caption, shadow(colors), { backgroundColor: colors.surface, maxHeight }]}
      >
        <ScrollView
          testID="agent-reply-scroll"
          style={[styles.captionScroll, fit?.scrollH !== undefined && { height: fit.scrollH }]}
          contentContainerStyle={[styles.captionBody, onDismiss && styles.captionBodyDismiss]}
          onContentSizeChange={(_w, h) => setContentH(h)}
          showsVerticalScrollIndicator
          persistentScrollbar
          bounces={false}
        >
          {heard ? (
            <Text testID="heard-transcript" numberOfLines={1} style={[styles.heardText, { color: colors.onSurfaceSecondary }]}>
              You said: “{heard}”
            </Text>
          ) : null}
          <Text
            testID="agent-reply-text"
            onLayout={(e) => setTextY(e.nativeEvent.layout.y)}
            onTextLayout={(e) => {
              const h = e.nativeEvent.lines[0]?.height;
              if (h) setLine(h);
            }}
            style={[styles.captionText, { color: colors.onSurface }]}
          >
            {text}
          </Text>
          <MapsSources sources={sources} />
        </ScrollView>
        {/* Everything under the scroll area: its height is part of what has to fit. The scroll content has no
            bottom padding, so at rest the area ends exactly at a line boundary. */}
        <View testID="agent-reply-below" onLayout={(e) => setBelowH(e.nativeEvent.layout.height)} style={styles.below}>
          {!!note && (
            <Text testID="agent-reply-note" numberOfLines={2} style={[styles.noteText, { color: colors.onSurfaceSecondary }]}>
              {note}
            </Text>
          )}
        </View>
        {onDismiss && (
          <Pressable
            testID="agent-reply-dismiss"
            accessibilityRole="button"
            accessibilityLabel="Close the reply"
            onPress={onDismiss}
            style={styles.dismiss}
          >
            <Icon name="close" size={22} color={colors.onSurfaceSecondary} />
          </Pressable>
        )}
      </View>
      <View style={[styles.tail, { backgroundColor: colors.surface }]} />
    </Animated.View>
  );
}

const styles = StyleSheet.create({
  wrap: { position: "absolute", left: spacing.lg, right: spacing.lg, alignItems: "center" },
  toast: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.sm,
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.md,
    borderRadius: radius.md,
    maxWidth: 420,
  },
  text: { fontFamily: fonts.semibold, fontSize: 14, flexShrink: 1 },
  captionWrap: { position: "absolute", left: spacing.lg, right: 88, alignItems: "flex-start" },
  caption: { borderRadius: radius.lg, borderBottomLeftRadius: radius.sm },
  captionScroll: { flexGrow: 0, flexShrink: 1 },
  captionBody: { paddingHorizontal: spacing.lg, paddingTop: spacing.md },
  below: { paddingTop: spacing.md },
  // Room on the right of the first lines for the 56 pt close button in the corner.
  captionBodyDismiss: { paddingRight: 56 },
  captionText: { fontFamily: fonts.medium, fontSize: 17, lineHeight: 24 },
  noteText: { fontFamily: fonts.regular, fontSize: 15, lineHeight: 21, paddingHorizontal: spacing.lg, paddingBottom: spacing.md, marginTop: -spacing.xs },
  // The whole 56 pt corner is the button (UI review 2026-11-05, N3: a 40 pt box was too small to hit at speed).
  dismiss: { position: "absolute", top: 0, right: 0, width: 56, height: 56, alignItems: "center", justifyContent: "center" },
  heardText: { fontFamily: fonts.regular, fontSize: 13, lineHeight: 18, marginBottom: 4 },
  tail: { width: 12, height: 12, marginLeft: 18, marginTop: -6, transform: [{ rotate: "45deg" }] },
});
