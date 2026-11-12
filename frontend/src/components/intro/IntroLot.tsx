import { useState } from "react";
import { Image, StyleSheet, useWindowDimensions, View } from "react-native";

import { useT } from "../../i18n";
import { Text } from "../../i18n/Text";
import { fonts, radius, shadow, spacing, useTheme } from "../../theme";
import { Icon } from "../Icon";
import { introStyles } from "./introStyles";

// Sacramento 49er Travel Plaza from above, 800x1000 px, about 250 m across. Public domain: USDA NAIP aerial imagery
// (tile m_3812128_ne_10_060_20220621, flown 2022-06-21, USDA-FSA-APFO), cut from the USGS National Map service
// https://imagery.nationalmap.gov/arcgis/rest/services/USGSNAIPImagery/ImageServer on 2026-11-12. Not Esri imagery.
const LOT = require("../../../assets/images/intro-lot.jpg");
const ASPECT = 800 / 1000;

// Where things are in that picture, as fractions of its width and height. The truck rows make a corner: a strip along
// the access road and the block east of the store.
const STRIP = { x: 0.58, y: 0.228, w: 0.37, h: 0.084 };
const BLOCK = { x: 0.58, y: 0.312, w: 0.155, h: 0.215 };
const WAY_IN = { x: 0.09, y: 0.215 }; // the access road where it leaves the street on the left
const LINE = 2;

/** Intro, screen 3 (Figma B3): the aerial picture with the truck parking and the way in, and the verdict on it. */
export function IntroLot() {
  const { colors } = useTheme();
  const tr = useT();
  const win = useWindowDimensions();
  // The picture keeps its shape (the marks sit on real trucks), as large as the space under the heading allows.
  const [space, setSpace] = useState<{ w: number; h: number } | null>(null);
  const maxW = space?.w ?? win.width - 2 * spacing.lg;
  const w = Math.max(200, Math.floor(Math.min(maxW, (space?.h ?? maxW / ASPECT) * ASPECT)));
  const h = Math.floor(w / ASPECT);
  const px = (r: { x: number; y: number; w: number; h: number }) => ({
    left: Math.round(r.x * w),
    top: Math.round(r.y * h),
    width: Math.round((r.x + r.w) * w) - Math.round(r.x * w),
    height: Math.round((r.y + r.h) * h) - Math.round(r.y * h),
  });
  const strip = px(STRIP);
  const block = px(BLOCK);
  const edge = { position: "absolute" as const, height: LINE, top: strip.top + strip.height - LINE, backgroundColor: colors.success };

  return (
    <View style={styles.root}>
      <Text accessibilityRole="header" style={[introStyles.title, styles.title, { color: colors.onSurface }]}>
        I check the lot first
      </Text>
      <View style={styles.space} onLayout={(e) => setSpace({ w: e.nativeEvent.layout.width, h: e.nativeEvent.layout.height })}>
        <View style={[styles.box, { width: w, height: h, backgroundColor: colors.mapPlaceholder }]}>
          <Image
            source={LOT}
            style={styles.fill}
            resizeMode="cover"
            accessible
            accessibilityRole="image"
            accessibilityLabel={tr("Aerial picture of a truck stop with the truck parking and the way in marked")}
          />
          {/* The marks repeat what the picture's label says: nothing here for a screen reader. */}
          <View testID="intro-lot-marks" style={styles.fill} pointerEvents="none" accessibilityElementsHidden importantForAccessibility="no-hide-descendants">
            <View style={[styles.abs, strip, styles.tint, { backgroundColor: colors.success }]} />
            <View style={[styles.abs, block, styles.tint, { backgroundColor: colors.success }]} />
            <View style={[styles.abs, strip, styles.stripLine, { borderColor: colors.success }]} />
            <View style={[styles.abs, block, styles.blockLine, { borderColor: colors.success }]} />
            <View style={[edge, { left: strip.left, width: block.left - strip.left + LINE }]} />
            <View style={[edge, { left: block.left + block.width - LINE, width: strip.left + strip.width - block.left - block.width + LINE }]} />
            <View style={[styles.tag, { right: w - strip.left - strip.width, top: strip.top - 32, backgroundColor: colors.success }]}>
              <Text style={[styles.tagText, { color: colors.onSuccess }]}>Truck parking</Text>
            </View>
            <View style={[styles.arrow, { left: Math.round(WAY_IN.x * w) - 18, top: Math.round(WAY_IN.y * h) - 18, backgroundColor: colors.info, borderColor: colors.onInfo }]}>
              <Icon name="arrow-right" size={22} color={colors.onInfo} />
            </View>
            <View style={[styles.tag, { left: spacing.md, top: Math.round(WAY_IN.y * h) + 24, backgroundColor: colors.info }]}>
              <Text style={[styles.tagText, { color: colors.onInfo }]}>Way in</Text>
            </View>
          </View>
          <View accessible style={[styles.card, shadow(colors, 2), { backgroundColor: colors.surface }]}>
            <Image source={require("../../../assets/images/avatar.png")} style={[styles.avatar, { backgroundColor: colors.avatarBg }]} />
            <View style={styles.flex}>
              <Text style={[styles.place, { color: colors.onSurface }]}>Sacramento 49er Travel Plaza</Text>
              <View style={styles.verdict}>
                <View style={[styles.dot, { backgroundColor: colors.success }]} />
                <Text style={[styles.verdictText, { color: colors.success }]}>Likely fits your truck</Text>
              </View>
            </View>
          </View>
        </View>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, paddingHorizontal: spacing.lg, paddingBottom: spacing.lg },
  title: { paddingHorizontal: spacing.sm },
  space: { flex: 1, alignItems: "center", justifyContent: "center", marginTop: spacing.lg },
  box: { borderRadius: radius.lg + spacing.sm, overflow: "hidden" },
  fill: { position: "absolute", left: 0, top: 0, width: "100%", height: "100%" },
  abs: { position: "absolute" },
  tint: { opacity: 0.32 },
  stripLine: { borderTopWidth: LINE, borderLeftWidth: LINE, borderRightWidth: LINE },
  blockLine: { borderBottomWidth: LINE, borderLeftWidth: LINE, borderRightWidth: LINE },
  tag: { position: "absolute", borderRadius: 10, paddingHorizontal: 9, paddingVertical: 5 },
  tagText: { fontFamily: fonts.extrabold, fontSize: 13 },
  arrow: { position: "absolute", width: 36, height: 36, borderRadius: 18, borderWidth: 2, alignItems: "center", justifyContent: "center" },
  card: { position: "absolute", left: spacing.md, right: spacing.md, bottom: spacing.md, flexDirection: "row", alignItems: "center", gap: spacing.md, borderRadius: radius.lg, padding: spacing.md },
  avatar: { width: 52, height: 52, borderRadius: 26 },
  flex: { flex: 1 },
  place: { fontFamily: fonts.bold, fontSize: 18, lineHeight: 23 },
  verdict: { flexDirection: "row", alignItems: "center", gap: spacing.sm, marginTop: 2 },
  dot: { width: 11, height: 11, borderRadius: 6 },
  verdictText: { flex: 1, fontFamily: fonts.bold, fontSize: 16, lineHeight: 22 },
});
