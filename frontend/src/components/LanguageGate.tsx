import { useState } from "react";
import { Image, ScrollView, StyleSheet, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { LANGS, setLang, t, UK_READY, useLang, type Lang } from "../i18n";
import { Text } from "../i18n/Text";
import { fontFor, fonts, radius, spacing, useTheme } from "../theme";
import { Icon } from "./Icon";
import { PressableScale } from "./PressableScale";

/**
 * The very first screen: the driver picks the copilot's language before anything else is said or shown, since the
 * consent text, the onboarding voice and every answer depend on it. Shown once; the choice can be changed later in
 * Truck profile → Language. The title is in every language offered (the driver may not read the phone's one).
 * Ukrainian appears here once it has its own texts (UK_READY), never as Russian.
 *
 * Figma f5Uha6gopQHRmcGlMXdCD9, B0: one row per language with a check mark, the phone's language picked already,
 * and "Continue" saves the choice. A tap on a row only moves the mark.
 */
export function LanguageGate() {
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const langs = LANGS.filter((l) => l.code !== "uk" || UK_READY);
  // The store holds the phone's language until a choice is saved (deviceLang).
  const phoneLang = useLang();
  const [picked, setPicked] = useState<Lang>(phoneLang);
  const [title, titleRu] = TITLE.split("\n");

  return (
    <View testID="language-gate" style={[styles.root, { backgroundColor: colors.surface, paddingTop: insets.top, paddingBottom: insets.bottom + spacing.xl }]}>
      <ScrollView contentContainerStyle={styles.top} showsVerticalScrollIndicator={false}>
        <Image source={require("../../assets/images/avatar.png")} style={[styles.avatar, { backgroundColor: colors.avatarBg }]} />
        {/* One text, so a screen reader says both lines as one heading. The Russian line has its own font:
            Plus Jakarta Sans has no Cyrillic. */}
        <Text accessibilityRole="header" style={[styles.title, { color: colors.onSurface }]}>
          {title}
          {"\n"}
          <Text style={[styles.titleRu, { color: colors.onSurfaceSecondary, fontFamily: fontFor(fonts.regular, "ru") }]}>{titleRu}</Text>
        </Text>
        <View accessibilityRole="radiogroup" style={styles.rows}>
          {langs.map((l) => {
            const on = l.code === picked;
            return (
              <PressableScale
                key={l.code}
                testID={`language-${l.code}`}
                accessibilityRole="radio"
                accessibilityState={{ checked: on }}
                accessibilityLabel={t(l.label)}
                onPress={() => setPicked(l.code)}
                style={[styles.row, { backgroundColor: on ? colors.surface : colors.surfaceSecondary, borderColor: on ? colors.brandPrimary : colors.surfaceSecondary }]}
              >
                <Text style={[styles.rowText, { color: colors.onSurface, fontFamily: fontFor(fonts.bold, l.code) }]}>{l.label}</Text>
                {on ? (
                  <View testID={`language-${l.code}-check`} style={[styles.check, { backgroundColor: colors.brandPrimary, borderColor: colors.brandPrimary }]}>
                    <Icon name="check" size={20} color={colors.onBrandPrimary} />
                  </View>
                ) : (
                  <View style={[styles.check, { borderColor: colors.borderStrong }]} />
                )}
              </PressableScale>
            );
          })}
        </View>
      </ScrollView>
      {/* In the language picked, not the phone's: the store changes only when this is pressed. */}
      <PressableScale
        testID="language-continue"
        accessibilityRole="button"
        accessibilityLabel={t("Continue", undefined, picked)}
        onPress={() => setLang(picked)}
        style={[styles.button, { backgroundColor: colors.brandPrimary }]}
      >
        <Text style={[styles.buttonText, { color: colors.onBrandPrimary, fontFamily: fontFor(fonts.bold, picked) }]}>{t("Continue", undefined, picked)}</Text>
      </PressableScale>
    </View>
  );
}

// Not run through t(): it has to read in every language at once.
const TITLE = "Choose your language\nВыбери язык";

const styles = StyleSheet.create({
  root: { flex: 1, paddingHorizontal: spacing.xl, gap: spacing.md },
  top: { flexGrow: 1, alignItems: "center", justifyContent: "center", paddingVertical: spacing.xl },
  avatar: { width: 132, height: 132, borderRadius: 66, marginBottom: spacing.xl },
  title: { fontFamily: fonts.extrabold, fontSize: 30, lineHeight: 36, textAlign: "center" },
  titleRu: { fontSize: 18, lineHeight: 30 },
  rows: { alignSelf: "stretch", gap: spacing.md, marginTop: spacing.xl },
  row: { minHeight: 68, borderRadius: radius.lg, borderWidth: 2, flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingHorizontal: spacing.lg + spacing.xs },
  rowText: { fontSize: 21 },
  check: { width: 30, height: 30, borderRadius: 15, borderWidth: 2, alignItems: "center", justifyContent: "center" },
  button: { height: 56, borderRadius: radius.pill, alignItems: "center", justifyContent: "center" },
  buttonText: { fontSize: 17 },
});
