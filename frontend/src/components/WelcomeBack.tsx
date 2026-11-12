import { useQuery } from "@tanstack/react-query";
import { ActivityIndicator, Image, ScrollView, StyleSheet, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { fetchProfile, PROFILE_KEY, serverProfile, useRestorePending } from "../data/profile";
import { truckLabel, type TruckProfile } from "../hooks/useTruckProfile";
import { t } from "../i18n";
import { Text } from "../i18n/Text";
import { bypassRestore, givenName, setWelcomeBack, useAccount } from "../state/account";
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
 * the account, and one button to the map.
 *
 * The screen holds the app until the server's profile has landed (src/data/profile.ts, the restore block): on the
 * map, with the default truck still in place, a tap on the height card would have sent the default over the
 * driver's real truck. So: a spinner and no button while it is on its way; "Try again" when it could not be
 * fetched; "Open the map" once it is in. The card shows only what the server returned, never the default truck.
 * If the server has no profile after all, the driver is new and the root layout moves on to onboarding by itself.
 *
 * Shown once: an app killed after the profile landed opens on the map; killed before, it comes back here.
 */
export function WelcomeBack() {
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const account = useAccount();
  const restoring = useRestorePending();
  // The root layout started this pull when the token went in; a minute-old answer is not asked for again.
  const profile = useQuery({ queryKey: PROFILE_KEY, queryFn: fetchProfile, staleTime: 60_000, retry: 1 });
  const landed = profile.isSuccess && restoring === false;
  const failed = profile.isError && !profile.isFetching;
  const truck = landed ? serverProfile() : null;
  const name = (truck?.driver_name ?? "").trim() || givenName(account?.name);
  const prefs = truck ? prefsSummary(truck) : [];

  return (
    <View testID="welcome-back" style={[styles.root, { backgroundColor: colors.surface, paddingTop: insets.top, paddingBottom: insets.bottom + spacing.xl }]}>
      <ScrollView contentContainerStyle={styles.top} showsVerticalScrollIndicator={false}>
        <Image source={require("../../assets/images/avatar.png")} style={[styles.avatar, { backgroundColor: colors.avatarBg }]} />
        <Text testID="welcome-back-title" accessibilityRole="header" style={[styles.title, { color: colors.onSurface }]}>
          {name ? `Welcome back, ${name}` : "Welcome back"}
        </Text>
        {!landed && !failed && <ActivityIndicator testID="welcome-back-loading" color={colors.brandPrimary} style={styles.spinner} />}
        {failed && (
          <Text testID="welcome-back-error" accessibilityRole="alert" style={[styles.sub, { color: colors.onSurface }]}>
            {"Couldn't load your truck. Check the connection and try again."}
          </Text>
        )}
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
      {failed ? (
        <PressableScale
          testID="welcome-back-retry"
          accessibilityRole="button"
          accessibilityLabel={t("Try again")}
          onPress={() => profile.refetch()}
          style={[styles.button, { backgroundColor: colors.brandPrimary }]}
        >
          <Text style={[styles.buttonText, { color: colors.onBrandPrimary }]}>Try again</Text>
        </PressableScale>
      ) : null}
      {failed && (
        // No signal can last: the driver is not kept here. On the map nothing is sent over the server's truck
        // and the height card stays away until the truck lands; the app keeps asking for it in the background.
        <PressableScale
          testID="welcome-back-skip"
          accessibilityRole="button"
          accessibilityLabel={t("Open the map anyway")}
          onPress={bypassRestore}
          style={[styles.secondary, { borderColor: colors.border }]}
        >
          <Text style={[styles.secondaryText, { color: colors.onSurface }]}>Open the map anyway</Text>
        </PressableScale>
      )}
      {!failed && (
        <PressableScale
          testID="welcome-back-open-map"
          accessibilityRole="button"
          accessibilityLabel={t("Open the map")}
          accessibilityState={{ disabled: !landed, busy: !landed }}
          disabled={!landed}
          onPress={() => setWelcomeBack(false)}
          style={[styles.button, { backgroundColor: landed ? colors.brandPrimary : colors.surfaceTertiary }]}
        >
          <Text style={[styles.buttonText, { color: landed ? colors.onBrandPrimary : colors.muted }]}>Open the map</Text>
        </PressableScale>
      )}
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
  secondary: { height: 56, borderRadius: radius.pill, borderWidth: 1, alignItems: "center", justifyContent: "center" },
  secondaryText: { fontFamily: fonts.semibold, fontSize: 17 },
});
