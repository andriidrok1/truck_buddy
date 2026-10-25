import { StyleSheet, Text, View } from "react-native";
import Animated, { FadeInUp, FadeOutUp } from "react-native-reanimated";

import { fonts, radius, shadow, spacing, useTheme } from "../theme";
import { Icon } from "./Icon";

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

export function CaptionBubble({ text, heard, bottom }: { text: string | null; heard?: string; bottom: number }) {
  const { colors } = useTheme();
  if (!text) return null;
  return (
    <Animated.View
      key={text}
      entering={FadeInUp.duration(260)}
      exiting={FadeOutUp.duration(180)}
      style={[styles.captionWrap, { bottom }]}
      pointerEvents="none"
    >
      <View testID="agent-reply-caption" style={[styles.caption, shadow(colors), { backgroundColor: colors.surface }]}>
        {heard ? (
          <Text testID="heard-transcript" numberOfLines={2} style={[styles.heardText, { color: colors.onSurfaceSecondary }]}>
            You said: “{heard}”
          </Text>
        ) : null}
        <Text testID="agent-reply-text" numberOfLines={3} style={[styles.captionText, { color: colors.onSurface }]}>{text}</Text>
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
  caption: { paddingHorizontal: spacing.lg, paddingVertical: spacing.md, borderRadius: radius.lg, borderBottomLeftRadius: radius.sm },
  captionText: { fontFamily: fonts.medium, fontSize: 15, lineHeight: 21 },
  heardText: { fontFamily: fonts.regular, fontSize: 13, lineHeight: 18, marginBottom: 4 },
  tail: { width: 12, height: 12, marginLeft: 18, marginTop: -6, transform: [{ rotate: "45deg" }] },
});
