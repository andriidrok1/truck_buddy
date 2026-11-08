import * as WebBrowser from "expo-web-browser";
import { StyleSheet, View } from "react-native";

import { PRIVACY_URL, TERMS_URL } from "../constants";
import { Text } from "../i18n/Text";
import { fonts, spacing, useTheme } from "../theme";
import { PressableScale } from "./PressableScale";

/** Privacy policy and Terms of use, reachable from the truck profile after the consent screen is gone (App Review 5.1.1). */
export function LegalLinks() {
  const { colors } = useTheme();
  return (
    <View style={styles.row}>
      <PressableScale testID="profile-privacy" accessibilityRole="link" onPress={() => WebBrowser.openBrowserAsync(PRIVACY_URL).catch(() => {})} style={styles.hit}>
        <Text style={[styles.link, { color: colors.info }]}>Privacy policy</Text>
      </PressableScale>
      <PressableScale testID="profile-terms" accessibilityRole="link" onPress={() => WebBrowser.openBrowserAsync(TERMS_URL).catch(() => {})} style={styles.hit}>
        <Text style={[styles.link, { color: colors.info }]}>Terms of use</Text>
      </PressableScale>
    </View>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: "row", justifyContent: "center", gap: spacing.xl, marginTop: spacing.sm },
  // Text links still get a 56 pt target (in-cab rule); the label stays a link, the area around it is the hit zone.
  hit: { minHeight: 56, minWidth: 56, justifyContent: "center", paddingHorizontal: spacing.sm },
  link: { fontFamily: fonts.semibold, fontSize: 15 },
});
