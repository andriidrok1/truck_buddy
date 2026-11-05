import { useEffect } from "react";
import { StyleSheet, View } from "react-native";
import Animated, { Easing, useAnimatedStyle, useSharedValue, withDelay, withRepeat, withTiming, type SharedValue } from "react-native-reanimated";

import { useTheme } from "../theme";

const BARS = [0.45, 0.8, 1, 0.65, 0.9, 0.5, 0.75];

function Bar({ peak, delay, level }: { peak: number; delay: number; level?: SharedValue<number> }) {
  const { colors } = useTheme();
  const v = useSharedValue(0.25);
  useEffect(() => {
    if (level) return; // driven by the mic level instead
    v.value = withDelay(delay, withRepeat(withTiming(peak, { duration: 420, easing: Easing.inOut(Easing.sin) }), -1, true));
  }, [v, peak, delay, level]);
  const style = useAnimatedStyle(() => {
    // With a live level the bars follow the driver's voice: the driver sees the phone hears them.
    const s = level ? withTiming(0.15 + 0.85 * level.value * peak, { duration: 80 }) : v.value;
    return { transform: [{ scaleY: s }] };
  });
  return <Animated.View style={[styles.bar, { backgroundColor: colors.info }, style]} />;
}

/** `level`: the mic level 0..1 (useVoice). Without it the bars just breathe. */
export function Waveform({ level }: { level?: SharedValue<number> } = {}) {
  return (
    <View style={styles.row} testID="listening-waveform">
      {BARS.map((p, i) => (
        <Bar key={i} peak={p} delay={i * 90} level={level} />
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: "row", alignItems: "center", gap: 4, height: 28 },
  bar: { width: 4, height: 28, borderRadius: 2 },
});
