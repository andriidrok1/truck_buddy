import { useRouter } from "expo-router";
import { useState } from "react";
import { Alert, Platform, TextInput, View } from "react-native";
import { Text } from "@/src/i18n/Text";
import { KeyboardAwareScrollView, KeyboardStickyView } from "@/src/keyboard";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { requestCode, signedInWithCode } from "@/src/auth";
import { Icon } from "@/src/components/Icon";
import { PressableScale } from "@/src/components/PressableScale";
import { EldSection } from "@/src/components/EldSection";
import { LegalLinks } from "@/src/components/LegalLinks";
import { OfflineMapsSection } from "@/src/components/OfflineMapsSection";
import { styles } from "@/src/components/truckProfileStyles";
import { WAKE_SUPPORTED } from "@/src/hooks/useWakeWord";
import { saveProfile } from "@/src/data/profile";
import { deleteMyData, signedOutReset } from "@/src/state/firstLaunch";
import { DEFAULT_TRUCK, useTruckProfile, type TruckProfile } from "@/src/hooks/useTruckProfile";
import { requestTruckReroute, setHandsFree, setSpeedAlerts, useHandsFree, useSpeedAlerts } from "@/src/state/settingsStore";
import { Chip, PrefsForm, ToggleRow, type Prefs } from "@/src/components/PrefsForm";
import { parseMpg, truckErrors, truckFromInputs } from "@/src/truck/validate";
import { confirmHeight } from "@/src/truck/heightConfirmed";
import { spacing, useTheme, setThemePref, useThemePref, type ThemePref } from "@/src/theme";
import { groupedInput, LANGS, setLang, t, UK_READY, useLang } from "@/src/i18n";


function Field({
  label,
  value,
  onChange,
  unit,
  testID,
  error,
  decimal,
  grouped,
}: {
  label: string;
  value: string;
  onChange: (s: string) => void;
  unit: string;
  testID: string;
  error?: boolean;
  /** A decimal keyboard (mpg); the number pad has no point on iOS. */
  decimal?: boolean;
  /** Thousands shown split in Russian ("80 000"); the value itself stays digits (src/truck/validate.ts toInt). */
  grouped?: boolean;
}) {
  const { colors } = useTheme();
  const lang = useLang();
  return (
    <View style={styles.field}>
      <Text style={[styles.label, { color: colors.onSurfaceSecondary }]}>{label}</Text>
      <View style={[styles.inputRow, { backgroundColor: colors.surfaceSecondary, borderColor: error ? colors.error : colors.surfaceSecondary }]}>
        <TextInput
          testID={testID}
          value={grouped ? groupedInput(value, lang) : value}
          onChangeText={onChange}
          keyboardType={decimal ? "decimal-pad" : "number-pad"}
          returnKeyType="done"
          style={[styles.input, { color: colors.onSurface }]}
          placeholderTextColor={colors.muted}
        />
        <Text style={[styles.unit, { color: colors.muted }]}>{unit}</Text>
      </View>
    </View>
  );
}

/** A destructive action asks first: a native alert, or the browser's confirm on web (which shows `message` as is). */
const confirmDestructive = (title: string, message: string, action: string, onYes: () => void) => {
  if (Platform.OS === "web") {
    if (window.confirm(message)) onYes();
    return;
  }
  Alert.alert(t(title), t(message), [
    { text: t("Cancel"), style: "cancel" },
    { text: t(action), style: "destructive", onPress: onYes },
  ]);
};

const MPG_ERROR = "Fuel economy must be 3 to 15 mpg.";
const DATA_CREDITS =
  "Places: © OpenStreetMap contributors (ODbL), © Overture Maps Foundation, Foursquare, AllThePlaces, FHWA. Search and truck routes: © HERE, openrouteservice. Weather: National Weather Service. Chain controls: Caltrans. What drivers say: Google Maps.";

const DELETE_MSG = "This removes everything TruckBuddy stored for this device on our servers and on this phone. The app starts over like a fresh install. This can't be undone.";

const confirmDelete = (onYes: () => void) => confirmDestructive("Delete my data?", DELETE_MSG, "Delete", onYes);

const RESET_MSG = "Height, trailer, weight, hazmat and your name go back to the defaults. Nothing is saved until you tap Save truck.";

const confirmReset = (onYes: () => void) => confirmDestructive("Reset to defaults?", RESET_MSG, "Reset", onYes);

/** The server copy, then the phone back to a fresh install (src/state/firstLaunch.ts). */
const onDeleteMyData = async (router: ReturnType<typeof useRouter>) => {
  if (!(await deleteMyData(router))) {
    Alert.alert(t("Couldn't reach the server"), t("Your data is still there. Check your connection and try again."));
  }
};

const THEME_CHOICES: { value: ThemePref; label: string }[] = [
  { value: "light", label: "Light" },
  { value: "dark", label: "Dark" },
  { value: "system", label: "Auto" },
];

const pickPrefs = (t: TruckProfile): Prefs => ({
  avoid_tolls: t.avoid_tolls,
  avoid_ferries_tunnels: t.avoid_ferries_tunnels,
  favorite_chains: t.favorite_chains,
  break_hours: t.break_hours,
});

export default function TruckProfileScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { colors } = useTheme();
  const themePref = useThemePref();
  const lang = useLang();
  const truck = useTruckProfile();
  const handsFree = useHandsFree();
  const speedAlerts = useSpeedAlerts();

  const [feet, setFeet] = useState(String(Math.floor(truck.height_in / 12)));
  const [inches, setInches] = useState(String(truck.height_in % 12));
  const [trailer, setTrailer] = useState(String(truck.trailer_ft));
  const [weight, setWeight] = useState(String(truck.weight_lb));
  const [hazmat, setHazmat] = useState(truck.hazmat);
  const [mpg, setMpg] = useState(truck.mpg ? String(truck.mpg) : "");
  const [operator, setOperator] = useState(truck.operator ?? null);
  const [name, setName] = useState(truck.driver_name ?? "");
  const [prefs, setPrefs] = useState<Prefs>(pickPrefs(truck));

  // The saved profile loads async (e.g. when this screen is opened directly): sync the fields once it arrives.
  const [synced, setSynced] = useState(truck);
  if (synced !== truck) {
    setSynced(truck);
    setFeet(String(Math.floor(truck.height_in / 12)));
    setInches(String(truck.height_in % 12));
    setTrailer(String(truck.trailer_ft));
    setWeight(String(truck.weight_lb));
    setHazmat(truck.hazmat);
    setMpg(truck.mpg ? String(truck.mpg) : "");
    setOperator(truck.operator ?? null);
    setName(truck.driver_name ?? "");
    setPrefs(pickPrefs(truck));
  }

  const close = () => (router.canGoBack() ? router.back() : router.replace("/"));

  // The server's limits, one message per field (src/truck/validate.ts, shared with onboarding).
  const errors = truckErrors({ feet, inches, trailer, weight });
  const sizes = truckFromInputs({ feet, inches, trailer, weight });
  const bad = { feet: !!errors.height, inches: !!errors.height, trailer: !!errors.trailer, weight: !!errors.weight };
  const mpgValue = parseMpg(mpg);
  const valid = sizes !== null && mpgValue !== undefined;

  const onSave = async () => {
    if (!sizes || mpgValue === undefined) return;
    await saveProfile({
      ...truck,
      ...prefs,
      driver_name: name.trim() || null,
      ...sizes,
      hazmat,
      mpg: mpgValue,
      operator,
    });
    await confirmHeight(); // the driver saw and saved the height
    requestTruckReroute(); // the map reroutes an active route for the new size (silently)
    close();
  };

  const onReset = () => {
    setFeet(String(Math.floor(DEFAULT_TRUCK.height_in / 12)));
    setInches(String(DEFAULT_TRUCK.height_in % 12));
    setTrailer(String(DEFAULT_TRUCK.trailer_ft));
    setWeight(String(DEFAULT_TRUCK.weight_lb));
    setHazmat(DEFAULT_TRUCK.hazmat);
    setMpg("");
    setName("");
  };

  return (
    <View style={[styles.root, { backgroundColor: colors.surface }]} testID="truck-profile-screen">
      <View style={[styles.header, { paddingTop: insets.top + spacing.sm }]}>
        <PressableScale testID="truck-profile-back-button" accessibilityLabel={t("Back")} onPress={close} style={[styles.back, { backgroundColor: colors.surfaceSecondary }]}>
          <Icon name="arrow-left" size={24} color={colors.onSurface} />
        </PressableScale>
        <Text style={[styles.title, { color: colors.onSurface }]}>Your truck</Text>
        <View style={styles.back} />
      </View>

      <KeyboardAwareScrollView bottomOffset={96} contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
        <Text style={[styles.hint, { color: colors.onSurfaceSecondary }]}>
          Routes avoid low bridges and roads closed to trucks of this size and weight.
        </Text>

        {/* Skipped in the onboarding, or just wrong: the copilot uses the name in about every third answer. */}
        <View style={styles.field}>
          <Text style={[styles.label, { color: colors.onSurfaceSecondary }]}>Your name</Text>
          <View style={[styles.inputRow, { backgroundColor: colors.surfaceSecondary, borderColor: colors.surfaceSecondary }]}>
            <TextInput
              testID="truck-name-input"
              value={name}
              onChangeText={(v) => setName(v.slice(0, 40))}
              placeholder={t("First name")}
              placeholderTextColor={colors.muted}
              autoCapitalize="words"
              autoCorrect={false}
              returnKeyType="done"
              style={[styles.input, { color: colors.onSurface }]}
            />
          </View>
          <Text style={[styles.switchSub, { color: colors.onSurfaceSecondary }]}>The copilot uses it now and then.</Text>
        </View>

        <Text style={[styles.section, { color: colors.onSurface }]}>Height</Text>
        <View style={styles.row}>
          <View style={styles.flex}>
            <Field label="Feet" value={feet} onChange={setFeet} unit="ft" testID="truck-height-feet-input" error={bad.feet} />
          </View>
          <View style={styles.flex}>
            <Field label="Inches" value={inches} onChange={setInches} unit="in" testID="truck-height-inches-input" error={bad.inches} />
          </View>
        </View>

        <Field label="Trailer length" value={trailer} onChange={setTrailer} unit="ft" testID="truck-trailer-input" error={bad.trailer} />
        <Field label="Gross weight" value={weight} onChange={setWeight} unit="lb" testID="truck-weight-input" error={bad.weight} grouped />
        <Field label="Fuel economy" value={mpg} onChange={setMpg} unit="mpg" testID="truck-mpg-input" error={mpgValue === undefined} decimal />
        <Text style={[styles.switchSub, { color: colors.onSurfaceSecondary }]}>
          For the route cost estimate. Leave empty for 6.5.
        </Text>

        <View style={styles.toggleGap}>
          <ToggleRow
            title="My own truck"
            subtitle="Owner-operator. Off: you drive for a company"
            testID="truck-owner-switch"
            value={operator === "owner"}
            onChange={(v) => setOperator(v ? "owner" : "company")}
            onColor={colors.brandPrimary}
          />
        </View>

        <View style={styles.toggleGap}>
          <ToggleRow
            title="Hazardous materials"
            subtitle="Avoid tunnels and roads closed to hazmat"
            testID="truck-hazmat-switch"
            value={hazmat}
            onChange={setHazmat}
            onColor={colors.warning}
          />
        </View>

        {/* Only where the wake word can actually run: a switch that does nothing is a review rejection. */}
        {WAKE_SUPPORTED && (
          <View style={styles.toggleGap}>
            <ToggleRow
              title="Hands-free"
              subtitle="Start the copilot with a wake word instead of a tap"
              testID="hands-free-switch"
              value={handsFree}
              onChange={setHandsFree}
              onColor={colors.brandPrimary}
            />
          </View>
        )}

        <View style={styles.toggleGap}>
          <ToggleRow
            title="Speed limit alerts"
            subtitle="Say so after 10 s more than 5 mph over the posted limit"
            testID="speed-alerts-switch"
            value={speedAlerts}
            onChange={setSpeedAlerts}
            onColor={colors.brandPrimary}
          />
        </View>

        {/* The copilot's language: the app, the voice and the server's answers (src/i18n). Ukrainian is listed only
            once it has its own texts: a Ukrainian driver must never be answered in Russian. */}
        <Text style={[styles.section, { color: colors.onSurface }]}>Language</Text>
        <View style={styles.row}>
          {LANGS.filter((l) => l.code !== "uk" || UK_READY).map((l) => (
            <Chip key={l.code} label={l.label} on={lang === l.code} onPress={() => setLang(l.code)} testID={`lang-${l.code}`} />
          ))}
        </View>

        <Text style={[styles.section, { color: colors.onSurface }]}>Appearance</Text>
        <View style={styles.row}>
          {THEME_CHOICES.map((c) => (
            <Chip key={c.value} label={c.label} on={themePref === c.value} onPress={() => setThemePref(c.value)} testID={`theme-${c.value}`} />
          ))}
        </View>
        <Text style={[styles.switchSub, { color: colors.onSurfaceSecondary }]}>Auto follows the phone&apos;s light or dark mode.</Text>

        <Text style={[styles.section, { color: colors.onSurface }]}>Preferences</Text>
        <PrefsForm value={prefs} onChange={(patch) => setPrefs((p) => ({ ...p, ...patch }))} />

        <EldSection />

        <OfflineMapsSection />

        {/* One line per field, each translated on its own: glued into one string, the Russian pattern for the
            height took the weight message into its {max} and left it English (QA 2026-11-09, bug 2). */}
        {!valid && (
          <View testID="truck-profile-error">
            {[errors.height, errors.trailer, errors.weight, mpgValue === undefined ? MPG_ERROR : undefined]
              .filter((e): e is string => !!e)
              .map((e) => (
                <Text key={e} style={[styles.error, { color: colors.error }]}>
                  {e}
                </Text>
              ))}
          </View>
        )}

        {/* Reset lives down here with a confirmation: a text link in the header was small and wiped the form in one
            tap (UI review 2026-11-05). It only resets the form; nothing is saved until Save truck. */}
        <PressableScale
          testID="truck-profile-reset-button"
          accessibilityLabel={t("Reset truck to defaults")}
          onPress={() => confirmReset(onReset)}
          style={[styles.signOut, { borderColor: colors.border }]}
        >
          <Icon name="restore" size={20} color={colors.onSurface} />
          <Text style={[styles.signOutText, { color: colors.onSurface }]}>Reset to defaults</Text>
        </PressableScale>

        {/* A tester signed in with an access code. An account's Sign out and Delete account come with the account section (APP-3). */}
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
          onPress={() => confirmDelete(() => onDeleteMyData(router))}
          style={[styles.signOut, { borderColor: colors.error, marginTop: spacing.md }]}
        >
          <Icon name="delete-outline" size={20} color={colors.error} />
          <Text style={[styles.signOutText, { color: colors.error }]}>Delete my data</Text>
        </PressableScale>
        <Text style={[styles.deleteNote, { color: colors.muted }]}>
          Erases everything stored for this device on our servers (conversation memory, trip events, your name and truck profile) and on this phone: language, settings, the truck profile. The app starts over like a fresh install.
        </Text>
        {/* Credits the open place data asks for (backend/scripts/build_places.py: ODbL, CDLA, Apache-2.0), plus HERE and
            openrouteservice routing, NWS weather and Caltrans chain controls. */}
        <Text testID="data-credits" style={[styles.deleteNote, { color: colors.muted, marginTop: spacing.lg }]}>
          {DATA_CREDITS}
        </Text>
        <LegalLinks />
      </KeyboardAwareScrollView>

      <KeyboardStickyView offset={{ opened: insets.bottom }}>
        <View style={[styles.footer, { paddingBottom: insets.bottom + spacing.lg, backgroundColor: colors.surface }]}>
          <PressableScale
            testID="truck-profile-save-button"
            onPress={onSave}
            disabled={!valid}
            style={[styles.save, { backgroundColor: valid ? colors.brandPrimary : colors.surfaceTertiary }]}
          >
            <Text style={[styles.saveText, { color: valid ? colors.onBrandPrimary : colors.muted }]}>Save truck</Text>
          </PressableScale>
        </View>
      </KeyboardStickyView>
    </View>
  );
}
