import { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, Image, StyleSheet, Text, View } from "react-native";

import { CodeRequiredError, registerDevice } from "../api";
import { codeRequested, requestCode } from "../auth";
import { fonts, radius, spacing, useTheme } from "../theme";
import { AccessGate } from "./AccessGate";
import { PressableScale } from "./PressableScale";

/** No token yet: sign the device in automatically, or show the code screen when the driver asked for it
 *  or the server requires a code. */
export function DeviceGate() {
  const { colors } = useTheme();
  const [error, setError] = useState("");
  const [needsCode, setNeedsCode] = useState(false);
  const wantsCode = codeRequested();

  const register = useCallback(
    () =>
      registerDevice().catch((e: unknown) => {
        if (e instanceof CodeRequiredError) setNeedsCode(true);
        else setError(e instanceof Error ? e.message : "Something went wrong. Try again.");
      }),
    [],
  );

  useEffect(() => {
    if (!wantsCode) register();
  }, [wantsCode, register]);

  if (needsCode) return <AccessGate />;
  if (wantsCode) return <AccessGate onSkip={() => requestCode(false)} />;

  return (
    <View style={[styles.wrap, { backgroundColor: colors.surface }]}>
      <Image source={require("../../assets/images/avatar.png")} style={[styles.avatar, { backgroundColor: colors.avatarBg }]} />
      {error ? (
        <>
          <Text testID="device-gate-error" accessibilityRole="alert" style={[styles.text, { color: colors.onSurface }]}>
            {error}
          </Text>
          <PressableScale
            testID="device-gate-retry"
            onPress={() => {
              setError("");
              register();
            }} style={[styles.button, { backgroundColor: colors.brandPrimary }]}>
            <Text style={[styles.buttonText, { color: colors.onBrandPrimary }]}>Try again</Text>
          </PressableScale>
          <PressableScale testID="device-gate-code" onPress={() => requestCode()}>
            <Text style={[styles.link, { color: colors.info }]}>I have an access code</Text>
          </PressableScale>
        </>
      ) : (
        <ActivityIndicator testID="device-gate-loading" color={colors.brandPrimary} />
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { flex: 1, alignItems: "center", justifyContent: "center", paddingHorizontal: spacing.xl, gap: spacing.lg },
  avatar: { width: 120, height: 120, borderRadius: 60 },
  text: { fontFamily: fonts.semibold, fontSize: 17, textAlign: "center" },
  button: { alignSelf: "stretch", height: 56, borderRadius: radius.pill, alignItems: "center", justifyContent: "center" },
  buttonText: { fontFamily: fonts.bold, fontSize: 17 },
  link: { fontFamily: fonts.semibold, fontSize: 15, paddingVertical: spacing.sm },
});
