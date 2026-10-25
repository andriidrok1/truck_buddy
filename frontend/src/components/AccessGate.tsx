import { useState } from "react";
import { ActivityIndicator, Image, Platform, StyleSheet, Text, TextInput, View } from "react-native";
import { KeyboardAwareScrollView } from "react-native-keyboard-controller";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { redeemAccessCode } from "../api";
import { fonts, radius, spacing, useTheme } from "../theme";
import { PressableScale } from "./PressableScale";

/** First launch: the driver enters the shared access code before using the app. */
export function AccessGate() {
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const submit = async () => {
    if (!code.trim() || busy) return;
    setBusy(true);
    setError("");
    try {
      await redeemAccessCode(code.trim());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong. Try again.");
    } finally {
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
        placeholder="Access code"
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
        accessibilityLabel="Continue"
        onPress={submit}
        disabled={!code.trim() || busy}
        containerStyle={styles.buttonWrap}
        style={[styles.button, { backgroundColor: colors.brandPrimary, opacity: !code.trim() ? 0.4 : 1 }]}
      >
        {busy ? <ActivityIndicator color={colors.onBrandPrimary} /> : <Text style={[styles.buttonText, { color: colors.onBrandPrimary }]}>Continue</Text>}
      </PressableScale>
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
});
