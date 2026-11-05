import { ActivityIndicator, StyleSheet, View } from "react-native";

import { fonts, radius, spacing, useTheme } from "../theme";
import { Text } from "../i18n/Text";
import { Icon } from "./Icon";
import { PressableScale } from "./PressableScale";

export const HISTORY_ERROR = "Couldn't load your conversations.";
export const HISTORY_EMPTY = "Nothing yet. Talk to your copilot and it will remember.";

/** The history screen with no rows: nothing remembered yet, or the load failed (with a Retry button).
 *  Drawn outside the inverted list on purpose: a list's empty component inherits the inversion transform,
 *  and on Android that transform is a full flip, so the text came out mirrored (UI review 2026-11-05). */
export function HistoryEmpty({ error, retrying, onRetry }: { error: boolean; retrying?: boolean; onRetry: () => void }) {
  const { colors } = useTheme();
  return (
    <View testID="history-empty" style={styles.empty}>
      <Icon name={error ? "cloud-alert-outline" : "chat-outline"} size={40} color={colors.muted} />
      <Text testID="history-empty-text" style={[styles.text, { color: colors.onSurfaceSecondary }]}>
        {error ? HISTORY_ERROR : HISTORY_EMPTY}
      </Text>
      {error && (
        <PressableScale
          testID="history-retry"
          accessibilityRole="button"
          accessibilityLabel="Retry"
          onPress={onRetry}
          disabled={retrying}
          style={[styles.retry, { backgroundColor: colors.brandPrimary }]}
        >
          {retrying ? (
            <ActivityIndicator color={colors.onBrandPrimary} />
          ) : (
            <>
              <Icon name="refresh" size={22} color={colors.onBrandPrimary} />
              <Text style={[styles.retryText, { color: colors.onBrandPrimary }]}>Retry</Text>
            </>
          )}
        </PressableScale>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  empty: { flex: 1, alignItems: "center", justifyContent: "center", gap: spacing.lg, paddingHorizontal: spacing.xxl },
  text: { fontFamily: fonts.medium, fontSize: 17, lineHeight: 24, textAlign: "center" },
  retry: { minWidth: 160, height: 56, borderRadius: radius.pill, paddingHorizontal: spacing.xl, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: spacing.sm },
  retryText: { fontFamily: fonts.bold, fontSize: 17 },
});
