import * as Haptics from "expo-haptics";
import type { ReactNode } from "react";
import { Platform, Pressable, type PressableProps, type StyleProp, type ViewStyle } from "react-native";
import Animated, { useAnimatedStyle, useSharedValue, withSpring } from "react-native-reanimated";

type Props = Omit<PressableProps, "style" | "children"> & {
  style?: StyleProp<ViewStyle>;
  containerStyle?: StyleProp<ViewStyle>;
  children: ReactNode;
  haptic?: "light" | "medium" | "heavy";
};

export function haptic(kind: "light" | "medium" | "heavy" | "success" = "light") {
  if (Platform.OS === "web") return;
  if (kind === "success") Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success).catch(() => {});
  else
    Haptics.impactAsync(
      kind === "heavy" ? Haptics.ImpactFeedbackStyle.Heavy : kind === "medium" ? Haptics.ImpactFeedbackStyle.Medium : Haptics.ImpactFeedbackStyle.Light,
    ).catch(() => {});
}

export function PressableScale({ style, containerStyle, children, haptic: h = "light", onPressIn, onPressOut, onPress, ...rest }: Props) {
  const scale = useSharedValue(1);
  const anim = useAnimatedStyle(() => ({ transform: [{ scale: scale.get() }] }));
  return (
    <Pressable
      {...rest}
      style={containerStyle}
      onPress={(e) => {
        haptic(h);
        onPress?.(e);
      }}
      onPressIn={(e) => {
        scale.set(withSpring(0.94, { damping: 18, stiffness: 400 }));
        onPressIn?.(e);
      }}
      onPressOut={(e) => {
        scale.set(withSpring(1, { damping: 14, stiffness: 300 }));
        onPressOut?.(e);
      }}
    >
      <Animated.View style={[style, anim]}>{children}</Animated.View>
    </Pressable>
  );
}
