import { useEffect } from "react";
import { Pressable, ScrollView, StyleSheet, View } from "react-native";
import { Text } from "../i18n/Text";
import Animated, { FadeInUp, FadeOutUp } from "react-native-reanimated";

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

/** The copilot's words over the map. The whole answer, never cut to two lines: it scrolls inside the bubble when
 *  it is longer than the room above the sheet, and stays until the driver closes it or a new answer replaces it
 *  (src/dialog/caption.ts). A spoken line that came after the answer shows under it as `note`. */
export function CaptionBubble({
  text,
  heard,
  note,
  bottom,
  maxHeight,
  sources,
  onDismiss,
  onHeight,
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
}) {
  const { colors } = useTheme();
  useEffect(() => {
    if (!text) onHeight?.(0);
  }, [text, onHeight]);
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
        <ScrollView style={styles.captionScroll} contentContainerStyle={[styles.captionBody, onDismiss && styles.captionBodyDismiss]} showsVerticalScrollIndicator bounces={false}>
          {heard ? (
            <Text testID="heard-transcript" numberOfLines={1} style={[styles.heardText, { color: colors.onSurfaceSecondary }]}>
              You said: “{heard}”
            </Text>
          ) : null}
          <Text testID="agent-reply-text" style={[styles.captionText, { color: colors.onSurface }]}>
            {text}
          </Text>
          <MapsSources sources={sources} />
        </ScrollView>
        {!!note && (
          <Text testID="agent-reply-note" numberOfLines={2} style={[styles.noteText, { color: colors.onSurfaceSecondary }]}>
            {note}
          </Text>
        )}
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
  captionBody: { paddingHorizontal: spacing.lg, paddingVertical: spacing.md },
  // Room on the right of the first lines for the 56 pt close button in the corner.
  captionBodyDismiss: { paddingRight: 56 },
  captionText: { fontFamily: fonts.medium, fontSize: 17, lineHeight: 24 },
  noteText: { fontFamily: fonts.regular, fontSize: 15, lineHeight: 21, paddingHorizontal: spacing.lg, paddingBottom: spacing.md, marginTop: -spacing.xs },
  // The whole 56 pt corner is the button (UI review 2026-11-05, N3: a 40 pt box was too small to hit at speed).
  dismiss: { position: "absolute", top: 0, right: 0, width: 56, height: 56, alignItems: "center", justifyContent: "center" },
  heardText: { fontFamily: fonts.regular, fontSize: 13, lineHeight: 18, marginBottom: 4 },
  tail: { width: 12, height: 12, marginLeft: 18, marginTop: -6, transform: [{ rotate: "45deg" }] },
});
