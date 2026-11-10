import { useEffect, useRef } from "react";
import { Modal, Pressable, StyleSheet, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { Text } from "../i18n/Text";
import { fonts, radius, shadow, spacing, useTheme } from "../theme";
import { PressableScale } from "./PressableScale";
import { t } from "../i18n";

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
 *  2026-11-05, ui-end-route-dialog-light.png). A tap outside counts as the safe answer.
 *  The safe button is on top and the destructive one under it: the sheet opens from a button at the bottom of the
 *  screen, and with the red one on top it landed right under that finger, so a double tap went through the
 *  question (UI review 2026-11-05, N6). The red one also ignores taps for ARM_MS after the sheet opens. */
export function ConfirmSheet({ title, confirmLabel, cancelLabel, onConfirm, onCancel, testID }: Props) {
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const openedAt = useRef(0);
  useEffect(() => {
    if (title) openedAt.current = Date.now();
  }, [title]);
  const confirm = () => {
    if (Date.now() - openedAt.current < ARM_MS) return;
    onConfirm();
  };
  return (
    <Modal visible={!!title} transparent animationType="fade" onRequestClose={onCancel} statusBarTranslucent>
      <Pressable testID={`${testID}-backdrop`} accessibilityLabel={t(cancelLabel)} onPress={onCancel} style={[styles.backdrop, { backgroundColor: colors.scrim }]} />
      <View testID={testID} style={[styles.sheet, shadow(colors, 2), { backgroundColor: colors.surface, paddingBottom: insets.bottom + spacing.lg }]}>
        <Text style={[styles.title, { color: colors.onSurface }]}>{title ?? ""}</Text>
        <PressableScale testID={`${testID}-cancel`} accessibilityRole="button" onPress={onCancel} style={[styles.btn, { backgroundColor: colors.surfaceSecondary }]}>
          <Text style={[styles.btnText, { color: colors.onSurface }]}>{cancelLabel}</Text>
        </PressableScale>
        <PressableScale testID={`${testID}-confirm`} accessibilityRole="button" haptic="heavy" onPress={confirm} style={[styles.btn, { backgroundColor: colors.error }]}>
          <Text style={[styles.btnText, { color: colors.onError }]}>{confirmLabel}</Text>
        </PressableScale>
      </View>
    </Modal>
  );
}

/** A tap on the red button this soon after the sheet opened is the second half of a double tap, not an answer. */
export const ARM_MS = 600;

const styles = StyleSheet.create({
  backdrop: { position: "absolute", top: 0, left: 0, right: 0, bottom: 0 },
  sheet: { position: "absolute", left: 0, right: 0, bottom: 0, borderTopLeftRadius: 28, borderTopRightRadius: 28, paddingTop: spacing.xl, paddingHorizontal: spacing.xl, gap: spacing.md },
  title: { fontFamily: fonts.bold, fontSize: 20, textAlign: "center", marginBottom: spacing.sm },
  btn: { height: 56, borderRadius: radius.pill, alignItems: "center", justifyContent: "center" },
  btnText: { fontFamily: fonts.bold, fontSize: 17 },
});
