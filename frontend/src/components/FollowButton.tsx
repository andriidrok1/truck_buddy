import { StyleSheet } from "react-native";

import { shadow, spacing, useTheme } from "../theme";
import { Icon } from "./Icon";
import { PressableScale } from "./PressableScale";
import { t } from "../i18n";

/** The map's "Follow truck" button, above the sheet (or above a card); moved verbatim from app/index.tsx. */
export function FollowButton({ following, bottom, onPress }: { following: boolean; bottom: number; onPress: () => void }) {
  const { colors } = useTheme();
  return (
    <PressableScale
      testID="follow-button"
      accessibilityLabel={following ? t("Stop following") : t("Follow truck")}
      accessibilityState={{ selected: following }}
      onPress={onPress}
      // Above the card on the map when one asks: under it, only the button's edge showed (UI review 2026-11-05, M6).
      containerStyle={[styles.recenter, { bottom }]}
      style={[styles.recenterBtn, shadow(colors), { backgroundColor: following ? colors.info : colors.surface }]}
    >
      <Icon name={following ? "navigation-variant" : "crosshairs-gps"} size={24} color={following ? colors.onInfo : colors.info} />
    </PressableScale>
  );
}

const styles = StyleSheet.create({
  recenter: { position: "absolute", right: spacing.lg },
  recenterBtn: {
    width: 56,
    height: 56,
    borderRadius: 28,
    alignItems: "center",
    justifyContent: "center",
  },
});
