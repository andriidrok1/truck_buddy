import { Modal, Pressable, StyleSheet, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { Text } from "../i18n/Text";
import { fonts, radius, shadow, spacing, useTheme } from "../theme";
import { PressableScale } from "./PressableScale";

type Props = {
  /** Null hides the sheet. */
  title: string | null;
  confirmLabel: string;
  cancelLabel: string;
  onConfirm: () => void;
  onCancel: () => void;
  /** testID prefix: `<id>`, `<id>-confirm`, `<id>-cancel`. */
  testID: string;
};

/** A yes/no question from the bottom of the screen with two full-width 56 pt buttons: the destructive one red,
 *  the safe one quiet. Replaces the system dialog, whose text buttons were 48 dp and weighed the same (UI review
 *  2026-11-05, ui-end-route-dialog-light.png). A tap outside counts as the safe answer. */
export function ConfirmSheet({ title, confirmLabel, cancelLabel, onConfirm, onCancel, testID }: Props) {
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  return (
    <Modal visible={!!title} transparent animationType="fade" onRequestClose={onCancel} statusBarTranslucent>
      <Pressable testID={`${testID}-backdrop`} accessibilityLabel={cancelLabel} onPress={onCancel} style={[styles.backdrop, { backgroundColor: colors.scrim }]} />
      <View testID={testID} style={[styles.sheet, shadow(colors, 2), { backgroundColor: colors.surface, paddingBottom: insets.bottom + spacing.lg }]}>
        <Text style={[styles.title, { color: colors.onSurface }]}>{title ?? ""}</Text>
        <PressableScale testID={`${testID}-confirm`} accessibilityRole="button" haptic="heavy" onPress={onConfirm} style={[styles.btn, { backgroundColor: colors.error }]}>
          <Text style={[styles.btnText, { color: colors.onError }]}>{confirmLabel}</Text>
        </PressableScale>
        <PressableScale testID={`${testID}-cancel`} accessibilityRole="button" onPress={onCancel} style={[styles.btn, { backgroundColor: colors.surfaceSecondary }]}>
          <Text style={[styles.btnText, { color: colors.onSurface }]}>{cancelLabel}</Text>
        </PressableScale>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  backdrop: { position: "absolute", top: 0, left: 0, right: 0, bottom: 0 },
  sheet: { position: "absolute", left: 0, right: 0, bottom: 0, borderTopLeftRadius: 28, borderTopRightRadius: 28, paddingTop: spacing.xl, paddingHorizontal: spacing.xl, gap: spacing.md },
  title: { fontFamily: fonts.bold, fontSize: 20, textAlign: "center", marginBottom: spacing.sm },
  btn: { height: 56, borderRadius: radius.pill, alignItems: "center", justifyContent: "center" },
  btnText: { fontFamily: fonts.bold, fontSize: 17 },
});
