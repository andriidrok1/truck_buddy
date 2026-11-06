import { StyleSheet, View } from "react-native";

import { Text } from "../i18n/Text";
import { fonts, useTheme } from "../theme";
import { Icon } from "./Icon";
import { PressableScale } from "./PressableScale";

/** A round button beside the copilot's avatar (Close, End route); moved verbatim from CopilotSheet.tsx. */
export function SideButton({
  icon,
  label,
  testID,
  onPress,
  danger,
}: {
  icon: string;
  label: string;
  testID: string;
  onPress?: () => void;
  danger?: boolean;
}) {
  const { colors } = useTheme();
  return (
    <PressableScale testID={testID} accessibilityLabel={label} onPress={onPress} style={styles.side} haptic={danger ? "heavy" : "light"}>
      <View style={[styles.sideCircle, { backgroundColor: danger ? colors.error : colors.surfaceSecondary }]}>
        <Icon name={icon} size={24} color={danger ? colors.onError : colors.onSurface} />
      </View>
      <Text style={[styles.sideLabel, { color: danger ? colors.error : colors.onSurfaceSecondary }]}>{label}</Text>
    </PressableScale>
  );
}

/** The empty slot of the same width on the side that has no button. */
export function SideSpacer() {
  return <View style={styles.side} />;
}

const styles = StyleSheet.create({
  side: { width: 72, alignItems: "center", gap: 6 },
  sideCircle: { width: 56, height: 56, borderRadius: 28, alignItems: "center", justifyContent: "center" },
  sideLabel: { fontFamily: fonts.medium, fontSize: 12 },
});
