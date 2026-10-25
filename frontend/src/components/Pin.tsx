import { StyleSheet, Text, View } from "react-native";

import { CATEGORY_ICON, categoryColor } from "../constants";
import { fonts, radius, shadow, useTheme } from "../theme";
import type { Category } from "../types";
import { Icon } from "./Icon";

export const PIN_W = 40;
export const PIN_H = 46;

/** Teardrop pin; the tip is at the bottom-center of the 40x46 box. */
export function Pin({ category, selected }: { category: Category; selected?: boolean }) {
  const { colors } = useTheme();
  const bg = categoryColor(colors, category);
  const s = selected ? 1.12 : 1;
  return (
    <View style={[styles.box, { transform: [{ scale: s }] }]} pointerEvents="none">
      <View style={[styles.drop, shadow(colors), { backgroundColor: bg, borderColor: colors.surface }]}>
        <View style={styles.iconWrap}>
          <Icon name={CATEGORY_ICON[category]} size={16} color={colors.onCategory} />
        </View>
      </View>
    </View>
  );
}

/** Blue navigation arrow with halo for the driver position. */
export function UserArrow({ heading }: { heading?: number | null }) {
  const { colors } = useTheme();
  return (
    <View style={[styles.halo, { backgroundColor: colors.userHalo }]} pointerEvents="none">
      <View style={[styles.arrowDisc, shadow(colors), { backgroundColor: colors.surface }]}>
        <Icon
          name="navigation"
          size={22}
          color={colors.routeLine}
          style={{ transform: [{ rotate: `${heading && heading > 0 ? heading : 0}deg` }] }}
        />
      </View>
    </View>
  );
}

export const USER_SIZE = 56;
export const ENTRY_SIZE = 26;

/** "E" marker: the truck entrance found on the satellite image (route ends here). */
export function EntryMarker() {
  const { colors } = useTheme();
  return (
    <View testID="map-entry-marker" style={[styles.entry, shadow(colors), { backgroundColor: colors.info, borderColor: colors.surface }]} pointerEvents="none">
      <Text style={[styles.entryText, { color: colors.onInfo }]}>E</Text>
    </View>
  );
}

export function DestinationLabel({ name }: { name: string }) {
  const { colors } = useTheme();
  return (
    <View style={styles.labelBox} pointerEvents="none">
      <View style={[styles.label, shadow(colors), { backgroundColor: colors.surface }]}>
        <Text numberOfLines={1} style={[styles.labelText, { color: colors.onSurface }]} testID="map-destination-label">
          {name}
        </Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  box: { width: PIN_W, height: PIN_H },
  drop: {
    position: "absolute",
    left: 4,
    top: 7,
    width: 32,
    height: 32,
    borderTopLeftRadius: 16,
    borderTopRightRadius: 16,
    borderBottomLeftRadius: 16,
    borderBottomRightRadius: 0,
    borderWidth: 2.5,
    transform: [{ rotate: "45deg" }],
    alignItems: "center",
    justifyContent: "center",
  },
  iconWrap: { transform: [{ rotate: "-45deg" }] },
  halo: { width: USER_SIZE, height: USER_SIZE, borderRadius: USER_SIZE / 2, alignItems: "center", justifyContent: "center" },
  arrowDisc: { width: 34, height: 34, borderRadius: 17, alignItems: "center", justifyContent: "center" },
  // left padding keeps the pill clear of the pin; bottom padding centers it on the pin head
  labelBox: { paddingLeft: 24, paddingBottom: 18 },
  label: { paddingHorizontal: 12, height: 32, borderRadius: radius.pill, justifyContent: "center", maxWidth: 200 },
  labelText: { fontFamily: fonts.semibold, fontSize: 13 },
  entry: { width: ENTRY_SIZE, height: ENTRY_SIZE, borderRadius: ENTRY_SIZE / 2, borderWidth: 2, alignItems: "center", justifyContent: "center" },
  entryText: { fontFamily: fonts.extrabold, fontSize: 13 },
});
