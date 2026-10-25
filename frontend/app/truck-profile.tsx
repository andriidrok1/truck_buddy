import { useRouter } from "expo-router";
import { useEffect, useState } from "react";
import { Platform, StyleSheet, Switch, Text, TextInput, View } from "react-native";
import { KeyboardAwareScrollView, KeyboardStickyView } from "react-native-keyboard-controller";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { Icon } from "@/src/components/Icon";
import { PressableScale } from "@/src/components/PressableScale";
import { DEFAULT_TRUCK, saveTruckProfile, useTruckProfile } from "@/src/hooks/useTruckProfile";
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

export default function TruckProfileScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { colors } = useTheme();
  const truck = useTruckProfile();

  const [feet, setFeet] = useState(String(Math.floor(truck.height_in / 12)));
  const [inches, setInches] = useState(String(truck.height_in % 12));
  const [trailer, setTrailer] = useState(String(truck.trailer_ft));
  const [weight, setWeight] = useState(String(truck.weight_lb));
  const [hazmat, setHazmat] = useState(truck.hazmat);

  // The saved profile loads async (e.g. when this screen is opened directly): sync the fields once it arrives.
  useEffect(() => {
    setFeet(String(Math.floor(truck.height_in / 12)));
    setInches(String(truck.height_in % 12));
    setTrailer(String(truck.trailer_ft));
    setWeight(String(truck.weight_lb));
    setHazmat(truck.hazmat);
  }, [truck]);

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
    await saveTruckProfile({ height_in: v.feet * 12 + v.inches, trailer_ft: v.trailer, weight_lb: v.weight, hazmat });
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
          />
        </View>

        {!valid && (
          <Text testID="truck-profile-error" style={[styles.error, { color: colors.error }]}>
            Check the highlighted values: height 5–16 ft, trailer 10–65 ft, weight 5,000–200,000 lb.
          </Text>
        )}
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
  error: { fontFamily: fonts.medium, fontSize: 13 },
  footer: { paddingHorizontal: spacing.lg, paddingTop: spacing.sm },
  save: { height: 56, borderRadius: radius.md, alignItems: "center", justifyContent: "center" },
  saveText: { fontFamily: fonts.bold, fontSize: 16 },
});
