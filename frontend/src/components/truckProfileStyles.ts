// Styles of the truck profile screen (app/truck-profile.tsx), shared with its sections in src/components.
import { Platform, StyleSheet } from "react-native";

import { fonts, radius, spacing } from "../theme";

const INPUT_BORDER = 1.5;

export const styles = StyleSheet.create({
  signOut: { height: 56, borderRadius: radius.pill, borderWidth: 1.5, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: spacing.sm, marginTop: spacing.xl },
  signOutText: { fontFamily: fonts.semibold, fontSize: 16 },
  deleteNote: { fontFamily: fonts.regular, fontSize: 13, textAlign: "center", marginTop: spacing.sm, paddingHorizontal: spacing.lg },
  root: { flex: 1 },
  header: { flexDirection: "row", alignItems: "center", paddingHorizontal: spacing.lg, paddingBottom: spacing.sm, gap: spacing.md },
  back: { width: 56, height: 56, borderRadius: 28, alignItems: "center", justifyContent: "center" },
  title: { flex: 1, fontFamily: fonts.bold, fontSize: 22 },
  content: { paddingHorizontal: spacing.lg, paddingTop: spacing.sm, paddingBottom: spacing.xl, gap: spacing.md },
  hint: { fontFamily: fonts.regular, fontSize: 14, lineHeight: 20 },
  section: { fontFamily: fonts.bold, fontSize: 16, marginTop: spacing.sm },
  row: { flexDirection: "row", gap: spacing.md },
  flex: { flex: 1 },
  field: { gap: 6 },
  label: { fontFamily: fonts.medium, fontSize: 13 },
  // The border is inside the box: 56 + both borders leaves the field itself 56 tall (UI review 2026-11-05, N10: 53).
  inputRow: { flexDirection: "row", alignItems: "center", height: 56 + 2 * INPUT_BORDER, borderRadius: radius.md, paddingHorizontal: spacing.lg, borderWidth: INPUT_BORDER, overflow: "hidden" },
  input: {
    flex: 1,
    minWidth: 0,
    width: 0,
    fontFamily: fonts.semibold,
    fontSize: 18,
    height: "100%",
    ...(Platform.OS === "web" ? ({ outlineStyle: "none" } as object) : {}),
  },
  unit: { fontFamily: fonts.medium, fontSize: 15, flexShrink: 0, marginLeft: spacing.sm },
  toggleGap: { marginTop: spacing.sm },
  switchTitle: { fontFamily: fonts.semibold, fontSize: 16 },
  switchSub: { fontFamily: fonts.regular, fontSize: 13, marginTop: 2 },
  eldBox: { borderRadius: radius.md, padding: spacing.lg, gap: spacing.md },
  eldStatusRow: { flexDirection: "row", alignItems: "center", gap: spacing.sm },
  eldBtn: { flex: 1, height: 56, borderRadius: radius.pill, alignItems: "center", justifyContent: "center" },
  eldBtnText: { fontFamily: fonts.semibold, fontSize: 15 },
  packDelete: { width: 56, height: 56, alignItems: "center", justifyContent: "center" },
  error: { fontFamily: fonts.medium, fontSize: 13 },
  footer: { paddingHorizontal: spacing.lg, paddingTop: spacing.sm },
  save: { height: 56, borderRadius: radius.md, alignItems: "center", justifyContent: "center" },
  saveText: { fontFamily: fonts.bold, fontSize: 16 },
});
