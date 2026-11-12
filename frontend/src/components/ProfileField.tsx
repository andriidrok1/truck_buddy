// One number field of the truck screen (app/truck-profile.tsx): label, input, unit.
import { TextInput, View } from "react-native";

import { groupedInput, useLang } from "../i18n";
import { Text } from "../i18n/Text";
import { useTheme } from "../theme";
import { styles } from "./truckProfileStyles";

export function Field({
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
