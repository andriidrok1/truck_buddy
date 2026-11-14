// The top of the truck screen: who this phone is signed in as (design 135:2, screen 8). An Apple or Google account
// shows its initial, name, email and provider; an access-code sign-in (testers, the emulator stand, App Review) says
// only that. "Synced" shows when the truck on this phone is the one the server has (src/data/profile.ts).

import { StyleSheet, View } from "react-native";

import { signedInWithAccount } from "../auth";
import { useProfileSynced } from "../data/profile";
import { t } from "../i18n";
import { Text } from "../i18n/Text";
import { useAccount, type Provider } from "../state/account";
import { fonts, radius, spacing, useTheme } from "../theme";
import { Icon } from "./Icon";

const PROVIDER: Record<Provider, string> = { apple: "Signed in with Apple", google: "Signed in with Google" };

/** The first letter of what is shown as the name, upper case; "" when there is nothing to take it from. */
export function initialOf(name: string | null | undefined): string {
  const first = Array.from((name ?? "").trim())[0] ?? "";
  return first.toLocaleUpperCase();
}

export function AccountSection({ driverName }: { driverName?: string | null }) {
  const { colors } = useTheme();
  const account = useAccount();
  const synced = useProfileSynced();
  // An account sign-in whose account_v1 is not on the phone yet (written right after the token, or a read that
  // failed) is still an account: "Your account", never "Access code" (review of #173, L1).
  const viaAccount = signedInWithAccount();
  const known = viaAccount ? account : null;

  // Apple gives the name on the first sign-in only; after that the name the driver gave the copilot stands in.
  const name = viaAccount ? known?.name || driverName?.trim() || known?.email || t("Your account") : t("Access code");
  const initial = known ? initialOf(name) : "";
  const provider = known ? t(PROVIDER[known.provider]) : viaAccount ? "" : t("Signed in with an access code");
  const sub = known?.email ? `${known.email} · ${provider}` : provider;

  return (
    <View testID="account-section" style={[s.box, { backgroundColor: colors.surfaceSecondary }]}>
      <View style={[s.avatar, { backgroundColor: colors.brandPrimary }]}>
        {initial ? (
          <Text testID="account-initial" style={[s.initial, { color: colors.onBrandPrimary }]}>
            {initial}
          </Text>
        ) : (
          <Icon name={viaAccount ? "account" : "key-variant"} size={26} color={colors.onBrandPrimary} />
        )}
      </View>
      <View style={s.text}>
        <Text testID="account-name" numberOfLines={1} style={[s.name, { color: colors.onSurface }]}>
          {name}
        </Text>
        {!!sub && (
          <Text testID="account-sub" numberOfLines={2} style={[s.sub, { color: colors.onSurfaceSecondary }]}>
            {sub}
          </Text>
        )}
      </View>
      {synced && (
        <View testID="account-synced" style={[s.pill, { backgroundColor: colors.surface }]}>
          <Icon name="check" size={14} color={colors.onSurface} />
          <Text style={[s.pillText, { color: colors.onSurface }]}>Synced</Text>
        </View>
      )}
    </View>
  );
}

const s = StyleSheet.create({
  box: { flexDirection: "row", alignItems: "center", gap: spacing.md, borderRadius: radius.lg, padding: spacing.lg },
  avatar: { width: 52, height: 52, borderRadius: 26, alignItems: "center", justifyContent: "center" },
  initial: { fontFamily: fonts.bold, fontSize: 22 },
  text: { flex: 1, minWidth: 0, gap: 2 },
  name: { fontFamily: fonts.bold, fontSize: 17 },
  sub: { fontFamily: fonts.regular, fontSize: 14 },
  pill: { flexDirection: "row", alignItems: "center", gap: 4, borderRadius: radius.pill, paddingHorizontal: spacing.sm + 2, paddingVertical: 5 },
  pillText: { fontFamily: fonts.semibold, fontSize: 12 },
});
