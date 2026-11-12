import { useQuery } from "@tanstack/react-query";
import { ActivityIndicator, Image, ScrollView, StyleSheet, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { fetchProfile, PROFILE_KEY } from "../data/profile";
import { truckLabel, type TruckProfile } from "../hooks/useTruckProfile";
import { t } from "../i18n";
import { Text } from "../i18n/Text";
import { givenName, setWelcomeBack, useAccount } from "../state/account";
import { fonts, radius, spacing, useTheme } from "../theme";
import { PressableScale } from "./PressableScale";

/** The preferences a driver set, in the words of the preferences form; empty when none. */
export function prefsSummary(p: TruckProfile): string[] {
  const out: string[] = [];
  if (p.avoid_tolls) out.push(t("Avoid toll roads"));
  if (p.avoid_ferries_tunnels) out.push(t("Avoid ferries and tunnels"));
  if (p.favorite_chains?.length) out.push(p.favorite_chains.join(", "));
  if (p.break_hours) out.push(t(`Every ${p.break_hours} h`));
  return out;
}

/**
 * A returning driver right after sign-in (Figma f5Uha6gopQHRmcGlMXdCD9, 135:2, screen 4): what came back with
 * the account, and one button to the map. Shown once: the flag lives in memory (src/state/account.ts), so an app
 * killed before the button opens on the map next time.
 *
 * The truck comes from the server (the root layout starts the pull the moment the token is in; this reads the
 * same request). Until it lands the card shows a spinner; if it fails, the screen says nothing it cannot back up,
 * and the map takes over (the pull runs again on the next start).
 */
export function WelcomeBack() {
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const account = useAccount();
  // The root layout started this pull when the token went in; a minute-old answer is not asked for again.
  const profile = useQuery({ queryKey: PROFILE_KEY, queryFn: fetchProfile, staleTime: 60_000 });
  const truck = profile.data;
  const name = (truck?.driver_name ?? "").trim() || givenName(account?.name);
  const prefs = truck ? prefsSummary(truck) : [];

  return (
    <View testID="welcome-back" style={[styles.root, { backgroundColor: colors.surface, paddingTop: insets.top, paddingBottom: insets.bottom + spacing.xl }]}>
      <ScrollView contentContainerStyle={styles.top} showsVerticalScrollIndicator={false}>
        <Image source={require("../../assets/images/avatar.png")} style={[styles.avatar, { backgroundColor: colors.avatarBg }]} />
        <Text testID="welcome-back-title" accessibilityRole="header" style={[styles.title, { color: colors.onSurface }]}>
          {name ? `Welcome back, ${name}` : "Welcome back"}
        </Text>
        {profile.isPending && <ActivityIndicator testID="welcome-back-loading" color={colors.brandPrimary} style={styles.spinner} />}
        {truck && (
          <>
            <Text style={[styles.sub, { color: colors.onSurfaceSecondary }]}>Here is what I brought over.</Text>
            <View testID="welcome-back-card" style={[styles.card, { backgroundColor: colors.surfaceSecondary }]}>
              <View style={styles.row}>
                <Text style={[styles.label, { color: colors.onSurfaceSecondary }]}>Truck</Text>
                <Text testID="welcome-back-truck" style={[styles.value, { color: colors.onSurface }]}>
                  {truckLabel(truck)}
                </Text>
              </View>
              {prefs.length > 0 && (
                <View style={[styles.row, styles.rowNext, { borderTopColor: colors.divider }]}>
                  <Text style={[styles.label, { color: colors.onSurfaceSecondary }]}>Preferences</Text>
                  <Text testID="welcome-back-prefs" style={[styles.value, { color: colors.onSurface }]}>
                    {prefs.join(" · ")}
                  </Text>
                </View>
              )}
            </View>
          </>
        )}
      </ScrollView>
      <PressableScale
        testID="welcome-back-open-map"
        accessibilityRole="button"
        accessibilityLabel={t("Open the map")}
        onPress={() => setWelcomeBack(false)}
        style={[styles.button, { backgroundColor: colors.brandPrimary }]}
      >
        <Text style={[styles.buttonText, { color: colors.onBrandPrimary }]}>Open the map</Text>
      </PressableScale>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, paddingHorizontal: spacing.xl, gap: spacing.md },
  top: { flexGrow: 1, alignItems: "center", justifyContent: "center", paddingVertical: spacing.xl },
  avatar: { width: 132, height: 132, borderRadius: 66, marginBottom: spacing.xl },
  title: { fontFamily: fonts.extrabold, fontSize: 28, lineHeight: 34, textAlign: "center" },
  sub: { fontFamily: fonts.regular, fontSize: 17, lineHeight: 24, textAlign: "center", marginTop: spacing.md },
  spinner: { marginTop: spacing.xl },
  card: { alignSelf: "stretch", borderRadius: radius.lg, paddingHorizontal: spacing.lg, marginTop: spacing.xl },
  row: { paddingVertical: spacing.lg, gap: spacing.xs },
  rowNext: { borderTopWidth: StyleSheet.hairlineWidth },
  label: { fontFamily: fonts.medium, fontSize: 14 },
  value: { fontFamily: fonts.semibold, fontSize: 17, lineHeight: 23 },
  button: { height: 56, borderRadius: radius.pill, alignItems: "center", justifyContent: "center" },
  buttonText: { fontFamily: fonts.bold, fontSize: 17 },
});
