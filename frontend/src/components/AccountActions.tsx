// The bottom of the truck screen (design 135:2, screens 8 to 10). An Apple or Google account: "Sign out", "Sign out
// of all phones" and "Delete account and data", each behind a sheet (src/state/accountActions.ts does the work).
// An access-code sign-in keeps what it had before accounts: "Sign out" at once and "Delete my data".

import { useState } from "react";
import { ActivityIndicator, Alert, View } from "react-native";

import { requestCode, signedInWithAccount, signedInWithCode } from "../auth";
import { t } from "../i18n";
import { Text } from "../i18n/Text";
import { deleteAccount, signOut, signOutEverywhere } from "../state/accountActions";
import { deleteMyData, signedOutReset, type Nav } from "../state/firstLaunch";
import { spacing, useTheme } from "../theme";
import { ConfirmSheet } from "./ConfirmSheet";
import { Icon } from "./Icon";
import { PressableScale } from "./PressableScale";
import { styles } from "./truckProfileStyles";
import { confirmDestructive } from "../utils/confirmDestructive";

const DELETE_MSG = "This removes everything TruckBuddy stored for this device on our servers and on this phone. The app starts over like a fresh install. This can't be undone.";

type Sheet = "signout" | "everywhere" | "delete" | "deleteFinal";

const SHEETS: Record<Sheet, { testID: string; title: string; message: string; confirm: string; tone: "neutral" | "destructive" }> = {
  signout: {
    testID: "sign-out-sheet",
    title: "Sign out?",
    message: "Your truck, places and history stay in your account. This phone goes back to an empty start.",
    confirm: "Sign out",
    tone: "neutral",
  },
  everywhere: {
    testID: "sign-out-all-sheet",
    title: "Sign out of all phones?",
    message: "Every phone signed in to this account goes back to the sign-in screen within a minute, this one too. Your data stays in your account.",
    confirm: "Sign out everywhere",
    tone: "neutral",
  },
  delete: {
    testID: "delete-account-sheet",
    title: "Delete your account?",
    message: "Your truck profile, preferences, history, trips and ledger are removed, on this phone and on our servers. This can't be undone.",
    confirm: "Continue",
    tone: "destructive",
  },
  deleteFinal: {
    testID: "delete-account-final",
    title: "Delete everything?",
    message: "Last step. Your account and everything in it are deleted now.",
    confirm: "Delete everything",
    tone: "destructive",
  },
};

const DELETE_FAILED = "Couldn't delete your account. Nothing was removed. Try again.";
const EVERYWHERE_FAILED = "Couldn't reach the server. You're still signed in everywhere. Try again.";

export function AccountActions({ router }: { router: Nav }) {
  const { colors } = useTheme();
  const [sheet, setSheet] = useState<Sheet | null>(null);
  const [busy, setBusy] = useState<"signout" | "everywhere" | "delete" | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (!signedInWithAccount()) return <CodeActions router={router} />;

  const run = async (what: "signout" | "everywhere" | "delete") => {
    setSheet(null);
    setError(null);
    setBusy(what);
    try {
      if (what === "signout") await signOut();
      else if (what === "everywhere") {
        if (!(await signOutEverywhere())) setError(EVERYWHERE_FAILED);
      } else {
        const out = await deleteAccount(router);
        if (!out.ok) setError(out.reason === "refused" ? out.detail : DELETE_FAILED);
      }
    } finally {
      setBusy(null); // on success the screen is already gone; harmless then
    }
  };

  const onConfirm = () => {
    if (sheet === "signout") run("signout");
    else if (sheet === "everywhere") run("everywhere");
    else if (sheet === "delete") setSheet("deleteFinal"); // the sheet re-arms itself for the second step
    else if (sheet === "deleteFinal") run("delete");
  };

  const cfg = sheet ? SHEETS[sheet] : null;
  const idle = busy === null;

  return (
    <View>
      <PressableScale
        testID="account-sign-out"
        accessibilityLabel={t("Sign out")}
        disabled={!idle}
        onPress={() => setSheet("signout")}
        style={[styles.signOut, { borderColor: colors.border }]}
      >
        {busy === "signout" ? <ActivityIndicator color={colors.onSurface} /> : <Icon name="logout" size={20} color={colors.onSurface} />}
        <Text style={[styles.signOutText, { color: colors.onSurface }]}>Sign out</Text>
      </PressableScale>

      {/* Quieter than Sign out: a text action, still 56 pt tall. */}
      <PressableScale
        testID="account-sign-out-all"
        accessibilityLabel={t("Sign out of all phones")}
        disabled={!idle}
        onPress={() => setSheet("everywhere")}
        style={styles.textAction}
      >
        {busy === "everywhere" && <ActivityIndicator color={colors.onSurfaceSecondary} />}
        <Text style={[styles.textActionText, { color: colors.onSurfaceSecondary }]}>Sign out of all phones</Text>
      </PressableScale>

      <PressableScale
        testID="account-delete"
        accessibilityLabel={t("Delete account and data")}
        disabled={!idle}
        onPress={() => setSheet("delete")}
        style={[styles.signOut, { borderColor: colors.error, marginTop: spacing.md }]}
      >
        {busy === "delete" ? <ActivityIndicator color={colors.error} /> : <Icon name="delete-outline" size={20} color={colors.error} />}
        <Text style={[styles.signOutText, { color: colors.error }]}>Delete account and data</Text>
      </PressableScale>
      {error ? (
        <Text testID="account-error" style={[styles.accountError, { color: colors.error }]}>
          {error}
        </Text>
      ) : (
        <Text style={[styles.deleteNote, { color: colors.muted }]}>
          Deletes your account and everything in it, on our servers and on this phone. The app starts over like a fresh install.
        </Text>
      )}

      <ConfirmSheet
        testID={cfg?.testID ?? "account-sheet"}
        title={cfg ? cfg.title : null}
        message={cfg?.message}
        confirmLabel={cfg?.confirm ?? ""}
        cancelLabel="Cancel"
        tone={cfg?.tone}
        onConfirm={onConfirm}
        onCancel={() => setSheet(null)}
      />
    </View>
  );
}

/** An access-code sign-in: no account to delete, the device's data goes with DELETE /api/memory. */
function CodeActions({ router }: { router: Nav }) {
  const { colors } = useTheme();
  const onDelete = async () => {
    if (!(await deleteMyData(router))) {
      Alert.alert(t("Couldn't reach the server"), t("Your data is still there. Check your connection and try again."));
    }
  };
  return (
    <View>
      {signedInWithCode() && (
        <PressableScale
          testID="sign-out"
          accessibilityLabel={t("Sign out")}
          onPress={async () => {
            // Nothing of this tester stays for whoever signs in next (the truck, the trip, the Motive key).
            await signedOutReset();
            requestCode(); // the sign-in screen opens on the code screen, in the builds that have one
          }}
          style={[styles.signOut, { borderColor: colors.border }]}
        >
          <Icon name="logout" size={20} color={colors.onSurface} />
          <Text style={[styles.signOutText, { color: colors.onSurface }]}>Sign out</Text>
        </PressableScale>
      )}

      {/* 5.1.1(v): a device identity with server records counts as an account; deletion must be in-app. */}
      <PressableScale
        testID="delete-my-data"
        accessibilityLabel={t("Delete my data")}
        onPress={() => confirmDestructive("Delete my data?", DELETE_MSG, "Delete", onDelete)}
        style={[styles.signOut, { borderColor: colors.error, marginTop: spacing.md }]}
      >
        <Icon name="delete-outline" size={20} color={colors.error} />
        <Text style={[styles.signOutText, { color: colors.error }]}>Delete my data</Text>
      </PressableScale>
      <Text style={[styles.deleteNote, { color: colors.muted }]}>
        Erases everything stored for this device on our servers (conversation memory, trip events, your name and truck profile) and on this phone: language, settings, the truck profile. The app starts over like a fresh install.
      </Text>
    </View>
  );
}
