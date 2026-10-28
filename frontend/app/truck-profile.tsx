import { useRouter } from "expo-router";
import { useState } from "react";
import { Alert, Platform, StyleSheet, Switch, Text, TextInput, View } from "react-native";
import { KeyboardAwareScrollView, KeyboardStickyView } from "@/src/keyboard";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { api } from "@/src/api";
import { requestCode, setToken } from "@/src/auth";
import { Icon } from "@/src/components/Icon";
import { setEldToken, useEldToken } from "@/src/eld";
import { PressableScale } from "@/src/components/PressableScale";
import { removePack, usePacks } from "@/src/hooks/useOfflineMap";
import { WAKE_SUPPORTED } from "@/src/hooks/useWakeWord";
import { DEFAULT_TRUCK, requestTruckReroute, saveTruckProfile, setHandsFree, setSpeedAlerts, useHandsFree, useSpeedAlerts, useTruckProfile } from "@/src/hooks/useTruckProfile";
import { formatBytes } from "@/src/map/bounds";
import { setSim } from "@/src/sim";
import { fonts, radius, spacing, useTheme } from "@/src/theme";

const LIMITS = {
  feet: [5, 16],
  inches: [0, 11],
  trailer: [10, 65],
  weight: [5000, 200000],
} as const;

const toInt = (s: string) => parseInt(s.replace(/[^0-9]/g, ""), 10);
const inRange = (v: number, [lo, hi]: readonly [number, number]) => Number.isFinite(v) && v >= lo && v <= hi;

function Field({
  label,
  value,
  onChange,
  unit,
  testID,
  error,
}: {
  label: string;
  value: string;
  onChange: (s: string) => void;
  unit: string;
  testID: string;
  error?: boolean;
}) {
  const { colors } = useTheme();
  return (
    <View style={styles.field}>
      <Text style={[styles.label, { color: colors.onSurfaceSecondary }]}>{label}</Text>
      <View style={[styles.inputRow, { backgroundColor: colors.surfaceSecondary, borderColor: error ? colors.error : colors.surfaceSecondary }]}>
        <TextInput
          testID={testID}
          value={value}
          onChangeText={onChange}
          keyboardType="number-pad"
          returnKeyType="done"
          style={[styles.input, { color: colors.onSurface }]}
          placeholderTextColor={colors.muted}
        />
        <Text style={[styles.unit, { color: colors.muted }]}>{unit}</Text>
      </View>
    </View>
  );
}

const DELETE_MSG = "This removes everything TruckBuddy stored for this device on our servers and signs you out. This can't be undone.";

const confirmDelete = (onYes: () => void) => {
  if (Platform.OS === "web") {
    if (window.confirm(DELETE_MSG)) onYes();
    return;
  }
  Alert.alert("Delete my data?", DELETE_MSG, [
    { text: "Cancel", style: "cancel" },
    { text: "Delete", style: "destructive", onPress: onYes },
  ]);
};

/** Server records first (while the token is still valid), then a fresh local state. */
const deleteMyData = async () => {
  try {
    await api.clearMemory();
  } catch {
    Alert.alert("Couldn't reach the server", "Your data is still there. Check your connection and try again.");
    return;
  }
  saveTruckProfile(DEFAULT_TRUCK);
  setSim(null);
  requestCode();
  await setToken(null);
};

export default function TruckProfileScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { colors } = useTheme();
  const truck = useTruckProfile();
  const handsFree = useHandsFree();
  const speedAlerts = useSpeedAlerts();
  const eldToken = useEldToken();
  const [eldDraft, setEldDraft] = useState("");
  const eldConnected = !!eldToken;
  const offlineMaps = usePacks();

  const [feet, setFeet] = useState(String(Math.floor(truck.height_in / 12)));
  const [inches, setInches] = useState(String(truck.height_in % 12));
  const [trailer, setTrailer] = useState(String(truck.trailer_ft));
  const [weight, setWeight] = useState(String(truck.weight_lb));
  const [hazmat, setHazmat] = useState(truck.hazmat);

  // The saved profile loads async (e.g. when this screen is opened directly): sync the fields once it arrives.
  const [synced, setSynced] = useState(truck);
  if (synced !== truck) {
    setSynced(truck);
    setFeet(String(Math.floor(truck.height_in / 12)));
    setInches(String(truck.height_in % 12));
    setTrailer(String(truck.trailer_ft));
    setWeight(String(truck.weight_lb));
    setHazmat(truck.hazmat);
  }

  const close = () => (router.canGoBack() ? router.back() : router.replace("/"));

  const v = { feet: toInt(feet), inches: toInt(inches || "0"), trailer: toInt(trailer), weight: toInt(weight) };
  const bad = {
    feet: !inRange(v.feet, LIMITS.feet),
    inches: !inRange(v.inches, LIMITS.inches),
    trailer: !inRange(v.trailer, LIMITS.trailer),
    weight: !inRange(v.weight, LIMITS.weight),
  };
  const valid = !Object.values(bad).some(Boolean);

  const onSave = async () => {
    if (!valid) return;
    await saveTruckProfile({ ...truck, height_in: v.feet * 12 + v.inches, trailer_ft: v.trailer, weight_lb: v.weight, hazmat });
    requestTruckReroute(); // the map reroutes an active route for the new size (silently)
    close();
  };

  const onReset = () => {
    setFeet(String(Math.floor(DEFAULT_TRUCK.height_in / 12)));
    setInches(String(DEFAULT_TRUCK.height_in % 12));
    setTrailer(String(DEFAULT_TRUCK.trailer_ft));
    setWeight(String(DEFAULT_TRUCK.weight_lb));
    setHazmat(DEFAULT_TRUCK.hazmat);
  };

  return (
    <View style={[styles.root, { backgroundColor: colors.surface }]} testID="truck-profile-screen">
      <View style={[styles.header, { paddingTop: insets.top + spacing.sm }]}>
        <PressableScale testID="truck-profile-back-button" accessibilityLabel="Back" onPress={close} style={[styles.back, { backgroundColor: colors.surfaceSecondary }]}>
          <Icon name="arrow-left" size={24} color={colors.onSurface} />
        </PressableScale>
        <Text style={[styles.title, { color: colors.onSurface }]}>Your truck</Text>
        <PressableScale testID="truck-profile-reset-button" onPress={onReset} style={styles.reset}>
          <Text style={[styles.resetText, { color: colors.info }]}>Reset</Text>
        </PressableScale>
      </View>

      <KeyboardAwareScrollView bottomOffset={96} contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
        <Text style={[styles.hint, { color: colors.onSurfaceSecondary }]}>
          Routes avoid low bridges and roads closed to trucks of this size and weight.
        </Text>

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
        <Field label="Gross weight" value={weight} onChange={setWeight} unit="lb" testID="truck-weight-input" error={bad.weight} />

        <View style={[styles.switchRow, { backgroundColor: colors.surfaceSecondary }]}>
          <View style={styles.flex}>
            <Text style={[styles.switchTitle, { color: colors.onSurface }]}>Hazardous materials</Text>
            <Text style={[styles.switchSub, { color: colors.onSurfaceSecondary }]}>Avoid tunnels and roads closed to hazmat</Text>
          </View>
          <Switch
            testID="truck-hazmat-switch"
            value={hazmat}
            onValueChange={setHazmat}
            trackColor={{ true: colors.warning, false: colors.surfaceTertiary }}
            thumbColor={colors.surface}
            {...(Platform.OS === "web" ? { activeThumbColor: colors.surface } : {})}
          />
        </View>

        {/* Only where the wake word can actually run: a switch that does nothing is a review rejection. */}
        {WAKE_SUPPORTED && (
          <View style={[styles.switchRow, { backgroundColor: colors.surfaceSecondary }]}>
            <View style={styles.flex}>
              <Text style={[styles.switchTitle, { color: colors.onSurface }]}>Hands-free</Text>
              <Text style={[styles.switchSub, { color: colors.onSurfaceSecondary }]}>
                Start the copilot with a wake word instead of a tap
              </Text>
            </View>
            <Switch
              testID="hands-free-switch"
              value={handsFree}
              onValueChange={setHandsFree}
              trackColor={{ true: colors.brandPrimary, false: colors.surfaceTertiary }}
              thumbColor={colors.surface}
              {...(Platform.OS === "web" ? { activeThumbColor: colors.surface } : {})}
            />
          </View>
        )}

        <View style={[styles.switchRow, { backgroundColor: colors.surfaceSecondary }]}>
          <View style={styles.flex}>
            <Text style={[styles.switchTitle, { color: colors.onSurface }]}>Speed limit alerts</Text>
            <Text style={[styles.switchSub, { color: colors.onSurfaceSecondary }]}>Say so after 10 s more than 5 mph over the posted limit</Text>
          </View>
          <Switch
            testID="speed-alerts-switch"
            value={speedAlerts}
            onValueChange={setSpeedAlerts}
            trackColor={{ true: colors.brandPrimary, false: colors.surfaceTertiary }}
            thumbColor={colors.surface}
            {...(Platform.OS === "web" ? { activeThumbColor: colors.surface } : {})}
          />
        </View>

        <Text style={[styles.section, { color: colors.onSurface }]}>Hours of service (Motive)</Text>
        <View testID="eld-section" style={[styles.eldBox, { backgroundColor: colors.surfaceSecondary }]}>
          <View style={styles.eldStatusRow}>
            <Icon name={eldConnected ? "check-circle" : "link-variant-off"} size={18} color={eldConnected ? colors.success : colors.muted} />
            <Text testID="eld-status" style={[styles.switchTitle, { color: colors.onSurface }]}>
              {eldConnected ? "Connected" : "Not connected"}
            </Text>
          </View>
          <View style={[styles.inputRow, { backgroundColor: colors.surface, borderColor: colors.surface }]}>
            <TextInput
              testID="eld-key-input"
              value={eldDraft}
              onChangeText={setEldDraft}
              placeholder={eldConnected ? "Paste a new API key to replace" : "Motive API key"}
              placeholderTextColor={colors.muted}
              secureTextEntry
              autoCapitalize="none"
              autoCorrect={false}
              autoComplete="off"
              textContentType="none"
              returnKeyType="done"
              style={[styles.input, { color: colors.onSurface }]}
            />
          </View>
          <View style={styles.row}>
            <PressableScale
              testID="eld-save-button"
              disabled={!eldDraft.trim()}
              onPress={async () => {
                await setEldToken(eldDraft);
                setEldDraft("");
              }}
              style={[styles.eldBtn, { backgroundColor: eldDraft.trim() ? colors.brandPrimary : colors.surfaceTertiary }]}
            >
              <Text style={[styles.eldBtnText, { color: eldDraft.trim() ? colors.onBrandPrimary : colors.muted }]}>Save</Text>
            </PressableScale>
            {eldConnected && (
              <PressableScale
                testID="eld-remove-button"
                onPress={async () => {
                  await setEldToken(null);
                  setEldDraft("");
                }}
                style={[styles.eldBtn, { borderColor: colors.error, borderWidth: 1.5 }]}
              >
                <Text style={[styles.eldBtnText, { color: colors.error }]}>Remove</Text>
              </PressableScale>
            )}
          </View>
          <Text style={[styles.switchSub, { color: colors.onSurfaceSecondary }]}>
            Your Motive API key stays in this phone&apos;s secure storage. It is sent only with your copilot requests so the copilot can read your hours, and is never stored on TruckBuddy servers. Remove it any time.
          </Text>
        </View>

        {/* MapLibre builds only: with the Apple provider there is no pack to list and nothing to delete. */}
        {offlineMaps.available && (
          <>
            <Text style={[styles.section, { color: colors.onSurface }]}>Offline maps</Text>
            <View testID="offline-maps-section" style={[styles.eldBox, { backgroundColor: colors.surfaceSecondary }]}>
              {offlineMaps.packs.length === 0 ? (
                <Text style={[styles.switchSub, { color: colors.onSurfaceSecondary }]}>
                  No maps saved. Set a route and tap “Download for offline” on its card; the last three routes are kept.
                </Text>
              ) : (
                offlineMaps.packs.map((p) => (
                  <View key={p.name} testID={`offline-pack-${p.name}`} style={styles.eldStatusRow}>
                    <Icon name={p.complete ? "map-check-outline" : "progress-download"} size={18} color={p.complete ? colors.success : colors.muted} />
                    <View style={styles.flex}>
                      <Text style={[styles.switchTitle, { color: colors.onSurface }]} numberOfLines={1}>
                        {p.name}
                      </Text>
                      <Text style={[styles.switchSub, { color: colors.onSurfaceSecondary }]}>
                        {p.complete ? formatBytes(p.bytes) : `${Math.round(p.percentage)}% · ${formatBytes(p.bytes)}`}
                        {p.createdAt ? ` · ${new Date(p.createdAt).toLocaleDateString()}` : ""}
                      </Text>
                    </View>
                    <PressableScale testID={`offline-pack-delete-${p.name}`} accessibilityLabel={`Delete ${p.name}`} onPress={() => removePack(p.name)} style={styles.packDelete}>
                      <Icon name="delete-outline" size={22} color={colors.error} />
                    </PressableScale>
                  </View>
                ))
              )}
              <Text style={[styles.switchSub, { color: colors.muted }]}>Map data © OpenFreeMap © OpenMapTiles © OpenStreetMap contributors.</Text>
            </View>
          </>
        )}

        {!valid && (
          <Text testID="truck-profile-error" style={[styles.error, { color: colors.error }]}>
            Check the highlighted values: height 5–16 ft, trailer 10–65 ft, weight 5,000–200,000 lb.
          </Text>
        )}

        <PressableScale
          testID="sign-out"
          accessibilityLabel="Sign out"
          onPress={async () => {
            setSim(null);
            requestCode(); // back to the access-code screen, not an automatic sign-in
            await setToken(null);
          }}
          style={[styles.signOut, { borderColor: colors.error }]}
        >
          <Icon name="logout" size={20} color={colors.error} />
          <Text style={[styles.signOutText, { color: colors.error }]}>Sign out (enter a code again)</Text>
        </PressableScale>

        {/* 5.1.1(v): a device identity with server records counts as an account; deletion must be in-app. */}
        <PressableScale
          testID="delete-my-data"
          accessibilityLabel="Delete my data"
          onPress={() => confirmDelete(deleteMyData)}
          style={[styles.signOut, { borderColor: colors.error, marginTop: spacing.md }]}
        >
          <Icon name="delete-outline" size={20} color={colors.error} />
          <Text style={[styles.signOutText, { color: colors.error }]}>Delete my data</Text>
        </PressableScale>
        <Text style={[styles.deleteNote, { color: colors.muted }]}>
          Erases everything stored for this device on our servers (conversation memory) and signs you out. The truck profile on this phone is reset too.
        </Text>
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

const styles = StyleSheet.create({
  signOut: { height: 52, borderRadius: radius.pill, borderWidth: 1.5, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: spacing.sm, marginTop: spacing.xl },
  signOutText: { fontFamily: fonts.semibold, fontSize: 16 },
  deleteNote: { fontFamily: fonts.regular, fontSize: 13, textAlign: "center", marginTop: spacing.sm, paddingHorizontal: spacing.lg },
  root: { flex: 1 },
  header: { flexDirection: "row", alignItems: "center", paddingHorizontal: spacing.lg, paddingBottom: spacing.sm, gap: spacing.md },
  back: { width: 48, height: 48, borderRadius: 24, alignItems: "center", justifyContent: "center" },
  title: { flex: 1, fontFamily: fonts.bold, fontSize: 22 },
  reset: { minHeight: 44, justifyContent: "center", paddingHorizontal: spacing.sm },
  resetText: { fontFamily: fonts.semibold, fontSize: 15 },
  content: { paddingHorizontal: spacing.lg, paddingTop: spacing.sm, paddingBottom: spacing.xl, gap: spacing.md },
  hint: { fontFamily: fonts.regular, fontSize: 14, lineHeight: 20 },
  section: { fontFamily: fonts.bold, fontSize: 16, marginTop: spacing.sm },
  row: { flexDirection: "row", gap: spacing.md },
  flex: { flex: 1 },
  field: { gap: 6 },
  label: { fontFamily: fonts.medium, fontSize: 13 },
  inputRow: { flexDirection: "row", alignItems: "center", height: 56, borderRadius: radius.md, paddingHorizontal: spacing.lg, borderWidth: 1.5, overflow: "hidden" },
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
  switchRow: { flexDirection: "row", alignItems: "center", gap: spacing.md, borderRadius: radius.md, padding: spacing.lg, marginTop: spacing.sm },
  switchTitle: { fontFamily: fonts.semibold, fontSize: 16 },
  switchSub: { fontFamily: fonts.regular, fontSize: 13, marginTop: 2 },
  eldBox: { borderRadius: radius.md, padding: spacing.lg, gap: spacing.md },
  eldStatusRow: { flexDirection: "row", alignItems: "center", gap: spacing.sm },
  eldBtn: { flex: 1, height: 48, borderRadius: radius.pill, alignItems: "center", justifyContent: "center" },
  eldBtnText: { fontFamily: fonts.semibold, fontSize: 15 },
  packDelete: { width: 44, height: 44, alignItems: "center", justifyContent: "center" },
  error: { fontFamily: fonts.medium, fontSize: 13 },
  footer: { paddingHorizontal: spacing.lg, paddingTop: spacing.sm },
  save: { height: 56, borderRadius: radius.md, alignItems: "center", justifyContent: "center" },
  saveText: { fontFamily: fonts.bold, fontSize: 16 },
});
