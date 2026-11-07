// The truck step of the onboarding when the driver types: height, trailer, weight (each with its own
// out-of-range message) and hazmat.
import { TextInput, View } from "react-native";
import Animated, { FadeInDown } from "react-native-reanimated";
import { Text } from "../i18n/Text";

import { useTheme } from "../theme";
import type { TruckField } from "../truck/validate";
import { styles } from "./onboardingStyles";
import { ToggleRow } from "./PrefsForm";

function Num({ label, value, onChange, unit, testID, bad = false }: { label: string; value: string; onChange: (s: string) => void; unit: string; testID: string; bad?: boolean }) {
  const { colors } = useTheme();
  return (
    <View style={styles.field}>
      <Text style={[styles.label, { color: colors.onSurfaceSecondary }]}>{label}</Text>
      <View style={[styles.inputRow, { backgroundColor: colors.surfaceSecondary }, bad && { borderWidth: 1.5, borderColor: colors.error }]}>
        <TextInput testID={testID} value={value} onChangeText={onChange} keyboardType="number-pad" style={[styles.input, { color: colors.onSurface }]} />
        <Text style={[styles.unit, { color: colors.muted }]}>{unit}</Text>
      </View>
    </View>
  );
}

/** The out-of-range message under its own field (one line for all three left the driver guessing). */
function FieldError({ text, testID }: { text?: string; testID: string }) {
  const { colors } = useTheme();
  if (!text) return null;
  return (
    <Text testID={testID} style={[styles.error, { color: colors.error }]}>
      {text}
    </Text>
  );
}

export function OnboardingTruckForm({
  feet,
  inches,
  trailer,
  weight,
  onFeet,
  onInches,
  onTrailer,
  onWeight,
  errors,
  hazmat,
  onHazmat,
}: {
  feet: string;
  inches: string;
  trailer: string;
  weight: string;
  onFeet: (s: string) => void;
  onInches: (s: string) => void;
  onTrailer: (s: string) => void;
  onWeight: (s: string) => void;
  errors: Partial<Record<TruckField, string>>;
  hazmat: boolean;
  onHazmat: (v: boolean) => void;
}) {
  return (
    <Animated.View entering={FadeInDown.duration(220)} style={styles.body}>
      <View style={styles.row}>
        <View style={styles.flex}>
          <Num label="Height" value={feet} onChange={onFeet} unit="ft" testID="onboarding-height-feet-input" bad={!!errors.height} />
        </View>
        <View style={styles.flex}>
          <Num label=" " value={inches} onChange={onInches} unit="in" testID="onboarding-height-inches-input" bad={!!errors.height} />
        </View>
      </View>
      <FieldError testID="onboarding-height-error" text={errors.height} />
      <Num label="Trailer length" value={trailer} onChange={onTrailer} unit="ft" testID="onboarding-trailer-input" bad={!!errors.trailer} />
      <FieldError testID="onboarding-trailer-error" text={errors.trailer} />
      <Num label="Gross weight" value={weight} onChange={onWeight} unit="lb" testID="onboarding-weight-input" bad={!!errors.weight} />
      <FieldError testID="onboarding-weight-error" text={errors.weight} />
      <ToggleRow title="Hazardous materials" value={hazmat} onChange={onHazmat} testID="onboarding-hazmat-switch" />
    </Animated.View>
  );
}
