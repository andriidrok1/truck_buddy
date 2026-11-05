import { Image, StyleSheet, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { LANGS, setLang, UK_READY, useLang, type Lang } from "../i18n";
import { Text } from "../i18n/Text";
import { fonts, radius, spacing, useTheme } from "../theme";
import { PressableScale } from "./PressableScale";

/**
 * The very first screen: the driver picks the copilot's language before anything else is said or shown, since the
 * consent text, the onboarding voice and every answer depend on it. Shown once; the choice can be changed later in
 * Truck profile → Language. The title is in every language offered (the driver may not read the phone's one).
 * Ukrainian appears here once it has its own texts (UK_READY), never as Russian.
 */
export function LanguageGate() {
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const current = useLang();
  const langs = LANGS.filter((l) => l.code !== "uk" || UK_READY);

  return (
    <View testID="language-gate" style={[styles.root, { backgroundColor: colors.surface, paddingTop: insets.top + spacing.xxxl, paddingBottom: insets.bottom + spacing.xl }]}>
      <View style={styles.top}>
        <Image source={require("../../assets/images/avatar.png")} style={[styles.avatar, { backgroundColor: colors.avatarBg }]} />
        <Text style={[styles.title, { color: colors.onSurface }]}>{TITLE}</Text>
      </View>
      <View style={styles.buttons}>
        {langs.map((l) => (
          <PressableScale
            key={l.code}
            testID={`language-${l.code}`}
            accessibilityRole="button"
            accessibilityLabel={l.label}
            onPress={() => setLang(l.code as Lang)}
            style={[styles.button, { backgroundColor: l.code === current ? colors.brandPrimary : colors.surfaceSecondary }]}
          >
            <Text style={[styles.buttonText, { color: l.code === current ? colors.onBrandPrimary : colors.onSurface }]}>{l.label}</Text>
          </PressableScale>
        ))}
      </View>
    </View>
  );
}

// Not run through t(): it has to read in every language at once.
const TITLE = "Choose your language\nВыберите язык";

const styles = StyleSheet.create({
  root: { flex: 1, paddingHorizontal: spacing.xl, justifyContent: "space-between" },
  top: { alignItems: "center", gap: spacing.lg },
  avatar: { width: 112, height: 112, borderRadius: 56 },
  title: { fontFamily: fonts.bold, fontSize: 26, lineHeight: 34, textAlign: "center" },
  buttons: { gap: spacing.md },
  button: { height: 64, borderRadius: radius.pill, alignItems: "center", justifyContent: "center" },
  buttonText: { fontFamily: fonts.bold, fontSize: 20 },
});
