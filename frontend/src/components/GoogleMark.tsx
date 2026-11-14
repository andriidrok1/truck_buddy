import { StyleSheet, View } from "react-native";

import { useTheme } from "../theme";

const SIZE = 20;
const STROKE = 4;

/**
 * A stand-in for Google's "G" on the sign-in button, drawn with views: a ring in the four brand colours, open on
 * the right, with the blue bar. PLACEHOLDER: Google's branding rules ask for their own logo asset, which must come
 * from their brand kit, not from the web. Replace this with that asset before the store build.
 */
export function GoogleMark() {
  const { colors } = useTheme();
  return (
    <View style={styles.box} accessible={false} importantForAccessibility="no-hide-descendants">
      <View
        style={[
          styles.ring,
          { borderTopColor: colors.googleRed, borderLeftColor: colors.googleYellow, borderBottomColor: colors.googleGreen, borderRightColor: colors.googleBlue },
        ]}
      />
      {/* The opening of the letter: the ring's upper right, down to the bar. */}
      <View style={[styles.gap, { backgroundColor: colors.googleButton }]} />
      <View style={[styles.bar, { backgroundColor: colors.googleBlue }]} />
    </View>
  );
}

const styles = StyleSheet.create({
  box: { width: SIZE, height: SIZE },
  ring: { position: "absolute", left: 0, top: 0, width: SIZE, height: SIZE, borderRadius: SIZE / 2, borderWidth: STROKE },
  gap: { position: "absolute", left: SIZE / 2, top: STROKE - 1, width: SIZE / 2, height: SIZE / 2 - STROKE + 1 },
  bar: { position: "absolute", left: SIZE / 2, top: (SIZE - STROKE) / 2, width: SIZE / 2, height: STROKE },
});
