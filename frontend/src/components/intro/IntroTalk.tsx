import { Image, ScrollView, StyleSheet } from "react-native";

import { Text } from "../../i18n/Text";
import { spacing, useTheme } from "../../theme";
import { introStyles } from "./introStyles";

/** Intro, screen 1 (Figma B1): the promise in one line, under the mascot. */
export function IntroTalk() {
  const { colors } = useTheme();
  return (
    <ScrollView contentContainerStyle={[introStyles.scroll, introStyles.centered]} showsVerticalScrollIndicator={false}>
      <Image source={require("../../../assets/images/avatar.png")} style={[styles.avatar, { backgroundColor: colors.avatarBg }]} />
      <Text accessibilityRole="header" style={[introStyles.title, introStyles.center, { color: colors.onSurface }]}>
        Talk, don’t tap
      </Text>
      <Text style={[introStyles.text, introStyles.center, { color: colors.onSurfaceSecondary }]}>
        A copilot for truck drivers. Ask out loud, keep your hands on the wheel.
      </Text>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  avatar: { width: 176, height: 176, borderRadius: 88, marginBottom: spacing.xl },
});
