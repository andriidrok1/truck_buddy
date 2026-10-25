import { useState } from "react";
import { Platform, StyleSheet, Text, TextInput, View } from "react-native";

import { fonts, radius, shadow, spacing, useTheme } from "../theme";
import { Icon } from "./Icon";
import { PressableScale } from "./PressableScale";

type Props = {
  topInset: number;
  truckLabel: string;
  hazmat: boolean;
  /** Current destination name, shown in the search bar. */
  destination?: string | null;
  onSearch: (text: string) => void;
  onTruckPress: () => void;
  onHistoryPress: () => void;
  /** Tester login: shows the test-scenarios button. */
  tester: boolean;
  /** Active simulated location label (tester), shown instead of "Demo location". */
  simLabel?: string | null;
  onTesterPress: () => void;
  isDemo: boolean;
  onLayout: (h: number) => void;
};

/** Google-Maps-style top: "Where to?" search bar, truck settings on the right, memory below it. */
export function TopBar({ topInset, truckLabel, hazmat, destination, onSearch, onTruckPress, onHistoryPress, tester, simLabel, onTesterPress, isDemo, onLayout }: Props) {
  const { colors } = useTheme();
  const card = [shadow(colors), { backgroundColor: colors.surface }];
  const [text, setText] = useState("");

  const submit = () => {
    const q = text.trim();
    if (!q) return;
    setText("");
    onSearch(q);
  };

  return (
    <View style={[styles.wrap, { paddingTop: topInset + spacing.sm }]} pointerEvents="box-none" onLayout={(e) => onLayout(e.nativeEvent.layout.height)}>
      <View style={styles.row} pointerEvents="box-none">
        <View style={[card, styles.search]}>
          <Icon name="magnify" size={24} color={colors.muted} />
          <TextInput
            testID="search-input"
            value={text}
            onChangeText={setText}
            onSubmitEditing={submit}
        placeholder={destination ? `To: ${destination.split(",")[0]}` : "Where to?"}
            placeholderTextColor={destination ? colors.onSurface : colors.muted}
            returnKeyType="search"
            autoCorrect={false}
            style={[styles.input, { color: colors.onSurface }]}
            accessibilityLabel="Where to?"
          />
          {!!text.trim() && (
            <PressableScale testID="search-submit" accessibilityLabel="Search" onPress={submit} style={[styles.go, { backgroundColor: colors.brandPrimary }]}>
              <Icon name="arrow-right" size={20} color={colors.onBrandPrimary} />
            </PressableScale>
          )}
        </View>

        <PressableScale testID="truck-profile-button" accessibilityLabel={`Truck settings: ${truckLabel}`} onPress={onTruckPress} style={[card, styles.circle]}>
          <Icon name="truck" size={26} color={colors.onSurface} />
          {hazmat && (
            <View testID="truck-hazmat-badge" style={[styles.hazmat, { backgroundColor: colors.warning, borderColor: colors.surface }]}>
              <Icon name="biohazard" size={11} color={colors.onWarning} />
            </View>
          )}
        </PressableScale>
      </View>

      <View style={styles.second} pointerEvents="box-none">
        {simLabel ? (
          <PressableScale testID="sim-location-tag" accessibilityLabel="Test location" onPress={onTesterPress} style={[card, styles.demo]}>
            <View style={[styles.demoDot, { backgroundColor: colors.warning }]} />
            <Text style={[styles.demoText, { color: colors.onSurfaceSecondary }]} numberOfLines={1}>{simLabel}</Text>
          </PressableScale>
        ) : isDemo ? (
          <View testID="demo-location-tag" style={[card, styles.demo]}>
            <View style={[styles.demoDot, { backgroundColor: colors.warning }]} />
            <Text style={[styles.demoText, { color: colors.onSurfaceSecondary }]}>Demo location</Text>
          </View>
        ) : (
          <View />
        )}
        <View style={styles.side} pointerEvents="box-none">
          <PressableScale testID="history-button" accessibilityLabel="Conversation memory" onPress={onHistoryPress} style={[card, styles.small]}>
            <Icon name="chat-processing-outline" size={22} color={colors.onSurface} />
          </PressableScale>
          {tester && (
            <PressableScale testID="tester-button" accessibilityLabel="Test scenarios" onPress={onTesterPress} style={[card, styles.small]}>
              <Icon name="flask-outline" size={22} color={colors.onSurface} />
            </PressableScale>
          )}
        </View>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { position: "absolute", top: 0, left: 0, right: 0 },
  row: { flexDirection: "row", alignItems: "center", paddingHorizontal: spacing.lg, gap: spacing.md },
  search: {
    flex: 1,
    height: 56,
    borderRadius: radius.pill,
    flexDirection: "row",
    alignItems: "center",
    paddingLeft: spacing.lg,
    paddingRight: spacing.sm,
    gap: spacing.sm,
  },
  input: { flex: 1, minWidth: 0, height: 56, fontFamily: fonts.medium, fontSize: 17, ...(Platform.OS === "web" ? ({ outlineStyle: "none" } as object) : {}) },
  go: { width: 40, height: 40, borderRadius: 20, alignItems: "center", justifyContent: "center" },
  circle: { width: 56, height: 56, borderRadius: 28, alignItems: "center", justifyContent: "center" },
  hazmat: {
    position: "absolute",
    top: 6,
    right: 6,
    width: 18,
    height: 18,
    borderRadius: 9,
    borderWidth: 2,
    alignItems: "center",
    justifyContent: "center",
  },
  second: { flexDirection: "row", justifyContent: "space-between", alignItems: "flex-start", paddingHorizontal: spacing.lg, marginTop: spacing.md, gap: spacing.md },
  side: { gap: spacing.md },
  small: { width: 48, height: 48, borderRadius: 24, alignItems: "center", justifyContent: "center", marginRight: 4 },
  demo: { height: 28, maxWidth: 260, borderRadius: radius.pill, flexDirection: "row", alignItems: "center", paddingHorizontal: spacing.md, gap: 6, flexShrink: 1 },
  demoDot: { width: 8, height: 8, borderRadius: 4 },
  demoText: { fontFamily: fonts.medium, fontSize: 12 },
});
