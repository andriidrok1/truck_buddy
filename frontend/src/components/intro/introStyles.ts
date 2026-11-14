// Shared by the three intro screens (Figma f5Uha6gopQHRmcGlMXdCD9, B1-B3): one heading size, one body size.
import { StyleSheet } from "react-native";

import { fonts, spacing } from "../../theme";

export const introStyles = StyleSheet.create({
  // Each screen scrolls on its own if it does not fit (a short phone, a large system font).
  scroll: { flexGrow: 1, paddingHorizontal: spacing.xl, paddingBottom: spacing.lg },
  centered: { alignItems: "center", justifyContent: "center" },
  title: { fontFamily: fonts.extrabold, fontSize: 30, lineHeight: 36, letterSpacing: -0.4 },
  text: { fontFamily: fonts.regular, fontSize: 18, lineHeight: 26, marginTop: spacing.md },
  center: { textAlign: "center" },
});
