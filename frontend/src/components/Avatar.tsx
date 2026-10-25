import { Image } from "expo-image";
import { useEffect } from "react";
import { StyleSheet, View } from "react-native";
import Animated, {
  cancelAnimation,
  Easing,
  useAnimatedStyle,
  useSharedValue,
  withRepeat,
  withSequence,
  withTiming,
} from "react-native-reanimated";

import { shadow, useTheme } from "../theme";
import { Icon } from "./Icon";

type Mode = "idle" | "listening" | "thinking" | "speaking";

export function Avatar({ size = 112, mode = "idle", showMic = true }: { size?: number; mode?: Mode; showMic?: boolean }) {
  const { colors } = useTheme();
  const ring = useSharedValue(0);
  const body = useSharedValue(1);

  useEffect(() => {
    cancelAnimation(ring);
    cancelAnimation(body);
    if (mode === "idle") {
      ring.value = withTiming(0, { duration: 300 });
      body.value = withTiming(1, { duration: 300 });
      return;
    }
    const dur = mode === "listening" ? 900 : mode === "speaking" ? 320 : 650;
    ring.value = 0;
    ring.value = withRepeat(withTiming(1, { duration: dur * 1.6, easing: Easing.out(Easing.quad) }), -1, false);
    body.value = withRepeat(
      withSequence(
        withTiming(mode === "thinking" ? 1.06 : mode === "speaking" ? 1.04 : 1.03, { duration: dur, easing: Easing.inOut(Easing.quad) }),
        withTiming(1, { duration: dur, easing: Easing.inOut(Easing.quad) }),
      ),
      -1,
      false,
    );
  }, [mode, ring, body]);

  const ringStyle = useAnimatedStyle(() => ({
    opacity: mode === "idle" ? 0 : 0.55 * (1 - ring.value),
    transform: [{ scale: 1 + ring.value * 0.35 }],
  }));
  const bodyStyle = useAnimatedStyle(() => ({ transform: [{ scale: body.value }] }));

  const glow = size + 20;
  return (
    <View style={{ width: glow + 8, height: glow + 8, alignItems: "center", justifyContent: "center" }} pointerEvents="none">
      <Animated.View
        style={[styles.abs, { width: glow, height: glow, borderRadius: glow / 2, borderWidth: 3, borderColor: colors.avatarGlow }, ringStyle]}
      />
      <View style={[styles.abs, { width: glow, height: glow, borderRadius: glow / 2, backgroundColor: colors.avatarGlowSoft }]} />
      <Animated.View
        style={[
          styles.disc,
          shadow(colors, 1),
          { width: size, height: size, borderRadius: size / 2, backgroundColor: colors.avatarBg },
          bodyStyle,
        ]}
      >
        <Image
          source={require("../../assets/images/avatar.png")}
          style={{ width: size * 0.78, height: size * 0.78 }}
          contentFit="contain"
          testID="copilot-avatar-image"
        />
      </Animated.View>
      {showMic && (
        <View
          style={[
            styles.badge,
            shadow(colors),
            { backgroundColor: colors.info, borderColor: colors.surface, right: 4 + (glow - size) / 2, bottom: 4 + (glow - size) / 2 },
          ]}
        >
          <Icon name={mode === "listening" ? "stop" : "microphone"} size={16} color={colors.onInfo} />
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  abs: { position: "absolute" },
  disc: { alignItems: "center", justifyContent: "center", overflow: "hidden" },
  badge: {
    position: "absolute",
    width: 32,
    height: 32,
    borderRadius: 16,
    borderWidth: 2.5,
    alignItems: "center",
    justifyContent: "center",
  },
});
