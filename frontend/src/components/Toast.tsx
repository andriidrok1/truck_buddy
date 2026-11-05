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
}: {
  text: string | null;
  heard?: string;
  note?: string;
  bottom: number;
  /** Room the bubble may take on the map; a longer answer scrolls inside it. */
  maxHeight?: number;
  sources?: MapsSource[];
  onDismiss?: () => void;
}) {
  const { colors } = useTheme();
  if (!text) return null;
  return (
    <Animated.View
      key={text}
      entering={FadeInUp.duration(260)}
      exiting={FadeOutUp.duration(180)}
      style={[styles.captionWrap, { bottom }]}
      pointerEvents="box-none"
    >
      <View testID="agent-reply-caption" style={[styles.caption, shadow(colors), { backgroundColor: colors.surface, maxHeight }]}>
        <ScrollView style={styles.captionScroll} contentContainerStyle={styles.captionBody} showsVerticalScrollIndicator bounces={false}>
          {heard ? (
            <Text testID="heard-transcript" numberOfLines={1} style={[styles.heardText, { color: colors.onSurfaceSecondary }]}>
              You said: “{heard}”
            </Text>
          ) : null}
          <Text testID="agent-reply-text" style={[styles.captionText, { color: colors.onSurface }]}>
            {text}
          </Text>
          {!!note && (
            <Text testID="agent-reply-note" style={[styles.noteText, { color: colors.onSurfaceSecondary }]}>
              {note}
            </Text>
          )}
          <MapsSources sources={sources} />
        </ScrollView>
        {onDismiss && (
          <Pressable
            testID="agent-reply-dismiss"
            accessibilityRole="button"
            accessibilityLabel="Close the reply"
            onPress={onDismiss}
            hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
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
  caption: { flexDirection: "row", alignItems: "flex-start", borderRadius: radius.lg, borderBottomLeftRadius: radius.sm },
  captionScroll: { flexShrink: 1 },
  captionBody: { paddingLeft: spacing.lg, paddingVertical: spacing.md },
  captionText: { fontFamily: fonts.medium, fontSize: 17, lineHeight: 24 },
  noteText: { fontFamily: fonts.regular, fontSize: 15, lineHeight: 21, marginTop: spacing.xs },
  // 40 pt of icon area plus an 8 pt slop on each side: 56 pt to hit without a bulky button on the bubble.
  dismiss: { width: 40, height: 40, alignItems: "center", justifyContent: "center", marginTop: spacing.xs, marginRight: spacing.xs },
  heardText: { fontFamily: fonts.regular, fontSize: 13, lineHeight: 18, marginBottom: 4 },
  tail: { width: 12, height: 12, marginLeft: 18, marginTop: -6, transform: [{ rotate: "45deg" }] },
});
