import { useEffect, useRef, useState } from "react";
import { ActivityIndicator, BackHandler, Image, Platform, StyleSheet, TextInput } from "react-native";
import { Text } from "../i18n/Text";
import { KeyboardAwareScrollView } from "@/src/keyboard";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { signInWithCode } from "../signin";
import { fonts, radius, spacing, useTheme } from "../theme";
import { PressableScale } from "./PressableScale";
import { t } from "../i18n";

/** The driver enters an access code (a tester's, or the shared one). Reached from the sign-in screen in Expo Go and
 *  dev builds only (src/components/SignInGate.tsx). `onSkip` adds the link back to it. */
export function AccessGate({ onSkip }: { onSkip?: () => void }) {
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const busyRef = useRef(false); // what the Back listener reads

  // Android's Back: back to the sign-in screen, like the link below. The gates render outside the Stack, so without
  // this the system takes the press and the app goes to the background. While a code is being checked the press is
  // swallowed: the sign-in may land any moment. Without `onSkip` (nothing to go back to) the system has it.
  useEffect(() => {
    if (!onSkip) return;
    const sub = BackHandler.addEventListener("hardwareBackPress", () => {
      if (!busyRef.current) onSkip();
      return true;
    });
    return () => sub.remove();
  }, [onSkip]);

  const submit = async () => {
    if (!code.trim() || busy) return;
    setBusy(true);
    busyRef.current = true;
    setError("");
    try {
      await signInWithCode(code.trim());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong. Try again.");
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  };

  return (
    <KeyboardAwareScrollView
      bottomOffset={spacing.xl}
      keyboardShouldPersistTaps="handled"
      style={{ backgroundColor: colors.surface }}
      contentContainerStyle={[styles.wrap, { paddingTop: insets.top + spacing.xxxl, paddingBottom: insets.bottom + spacing.xl }]}
    >
      <Image source={require("../../assets/images/avatar.png")} style={[styles.avatar, { backgroundColor: colors.avatarBg }]} />
      <Text style={[styles.title, { color: colors.onSurface }]}>Welcome to TruckBuddy</Text>
      <Text style={[styles.sub, { color: colors.onSurfaceSecondary }]}>Enter your access code to get started.</Text>
      <TextInput
        testID="access-code-input"
        value={code}
        onChangeText={(t) => {
          setCode(t);
          setError("");
        }}
        placeholder={t("Access code")}
        placeholderTextColor={colors.muted}
        autoCapitalize="characters"
        autoCorrect={false}
        autoComplete="off"
        returnKeyType="go"
        onSubmitEditing={submit}
        style={[styles.input, { color: colors.onSurface, backgroundColor: colors.surfaceSecondary, borderColor: error ? colors.error : colors.surfaceSecondary }]}
      />
      {!!error && (
        <Text testID="access-code-error" accessibilityRole="alert" style={[styles.error, { color: colors.error }]}>
          {error}
        </Text>
      )}
      <PressableScale
        testID="access-code-submit"
        accessibilityLabel={t("Continue")}
        onPress={submit}
        disabled={!code.trim() || busy}
        containerStyle={styles.buttonWrap}
        style={[styles.button, { backgroundColor: colors.brandPrimary, opacity: !code.trim() ? 0.4 : 1 }]}
      >
        {busy ? <ActivityIndicator color={colors.onBrandPrimary} /> : <Text style={[styles.buttonText, { color: colors.onBrandPrimary }]}>Continue</Text>}
      </PressableScale>
      {onSkip && (
        <PressableScale testID="access-code-skip" accessibilityRole="button" onPress={onSkip} style={styles.skipHit}>
          <Text style={[styles.skip, { color: colors.info }]}>Back to sign-in</Text>
        </PressableScale>
      )}
    </KeyboardAwareScrollView>
  );
}

const styles = StyleSheet.create({
  wrap: { flexGrow: 1, alignItems: "center", paddingHorizontal: spacing.xl, gap: spacing.md },
  avatar: { width: 120, height: 120, borderRadius: 60, marginBottom: spacing.lg },
  title: { fontFamily: fonts.bold, fontSize: 26, textAlign: "center" },
  sub: { fontFamily: fonts.regular, fontSize: 16, textAlign: "center", marginBottom: spacing.lg },
  input: {
    alignSelf: "stretch",
    height: 56,
    borderRadius: radius.md,
    borderWidth: 1.5,
    paddingHorizontal: spacing.lg,
    fontFamily: fonts.semibold,
    fontSize: 20,
    letterSpacing: 2,
    textAlign: "center",
    ...(Platform.OS === "web" ? ({ outlineStyle: "none" } as object) : {}),
  },
  error: { fontFamily: fonts.medium, fontSize: 14 },
  buttonWrap: { alignSelf: "stretch" },
  button: { height: 56, borderRadius: radius.pill, alignItems: "center", justifyContent: "center", marginTop: spacing.sm },
  buttonText: { fontFamily: fonts.bold, fontSize: 17 },
  skipHit: { minHeight: 56, minWidth: 56, alignItems: "center", justifyContent: "center", paddingHorizontal: spacing.sm },
  skip: { fontFamily: fonts.semibold, fontSize: 15 },
});
