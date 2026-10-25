import { Linking, StyleSheet, Text, View } from "react-native";
import Animated, { FadeInDown, FadeOutDown } from "react-native-reanimated";

import { MESSAGES } from "../constants";
import type { MicPermission } from "../hooks/useVoice";
import { fonts, radius, shadow, spacing, useTheme } from "../theme";
import { Icon } from "./Icon";
import { PressableScale } from "./PressableScale";

type Props = { state: Exclude<MicPermission, "granted">; bottom: number; onAllow: () => void; onDismiss: () => void };

// Pre-permission explanation (state "ask") or a route to Settings once the OS stops asking ("blocked").
export function MicPermissionCard({ state, bottom, onAllow, onDismiss }: Props) {
  const { colors } = useTheme();
  return (
    <Animated.View
      entering={FadeInDown.duration(240)}
      exiting={FadeOutDown.duration(180)}
      style={[styles.wrap, shadow(colors, 2), { bottom, backgroundColor: colors.surface }]}
      testID="mic-permission-card"
    >
      <View style={styles.row}>
        <View style={[styles.iconWrap, { backgroundColor: colors.surfaceSecondary }]}>
          <Icon name="microphone" size={24} color={colors.info} />
        </View>
        <Text testID="mic-permission-text" style={[styles.text, { color: colors.onSurface }]}>
          {MESSAGES.micNeeded}
        </Text>
      </View>
      <View style={styles.row}>
        <PressableScale
          testID="mic-permission-dismiss-button"
          onPress={onDismiss}
          containerStyle={styles.flex}
          style={[styles.btn, { backgroundColor: colors.surfaceSecondary }]}
        >
          <Text style={[styles.btnText, { color: colors.onSurface }]}>Not now</Text>
        </PressableScale>
        <PressableScale
          testID={state === "ask" ? "mic-permission-allow-button" : "mic-permission-settings-button"}
          onPress={state === "ask" ? onAllow : () => Linking.openSettings?.()?.catch(() => {})}
          containerStyle={styles.flex}
          style={[styles.btn, { backgroundColor: colors.brandPrimary }]}
        >
          <Text style={[styles.btnText, { color: colors.onBrandPrimary }]}>
            {state === "ask" ? "Allow microphone" : "Open Settings"}
          </Text>
        </PressableScale>
      </View>
    </Animated.View>
  );
}

const styles = StyleSheet.create({
  wrap: { position: "absolute", left: spacing.lg, right: spacing.lg, borderRadius: radius.lg, padding: spacing.lg, gap: spacing.md },
  row: { flexDirection: "row", alignItems: "center", gap: spacing.sm },
  iconWrap: { width: 44, height: 44, borderRadius: 22, alignItems: "center", justifyContent: "center" },
  text: { flex: 1, fontFamily: fonts.semibold, fontSize: 16, lineHeight: 22 },
  flex: { flex: 1 },
  btn: { height: 56, borderRadius: radius.md, alignItems: "center", justifyContent: "center" },
  btnText: { fontFamily: fonts.bold, fontSize: 15 },
});
