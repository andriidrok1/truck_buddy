import { useEffect } from "react";
import { StyleSheet, View } from "react-native";
import Animated, { Easing, useAnimatedStyle, useSharedValue, withDelay, withRepeat, withTiming } from "react-native-reanimated";

import { useTheme } from "../theme";

const BARS = [0.45, 0.8, 1, 0.65, 0.9, 0.5, 0.75];

function Bar({ peak, delay }: { peak: number; delay: number }) {
  const { colors } = useTheme();
  const v = useSharedValue(0.25);
  useEffect(() => {
    v.value = withDelay(delay, withRepeat(withTiming(peak, { duration: 420, easing: Easing.inOut(Easing.sin) }), -1, true));
  }, [v, peak, delay]);
  const style = useAnimatedStyle(() => ({ transform: [{ scaleY: v.value }] }));
  return <Animated.View style={[styles.bar, { backgroundColor: colors.info }, style]} />;
}

export function Waveform() {
  return (
    <View style={styles.row} testID="listening-waveform">
      {BARS.map((p, i) => (
        <Bar key={i} peak={p} delay={i * 90} />
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: "row", alignItems: "center", gap: 4, height: 28 },
  bar: { width: 4, height: 28, borderRadius: 2 },
});
