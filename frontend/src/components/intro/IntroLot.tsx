import { useState } from "react";
import { Image, StyleSheet, useWindowDimensions, View } from "react-native";

import { useT } from "../../i18n";
import { Text } from "../../i18n/Text";
import { fonts, radius, shadow, spacing, useTheme } from "../../theme";
import { Icon } from "../Icon";
import { introStyles } from "./introStyles";

// Sacramento 49er Travel Plaza from above, 800x1000 px, about 390 m across (0.49 m per pixel, close to the 0.6 m the
// plane shot). Public domain: USDA NAIP aerial imagery (tile m_3812128_ne_10_060_20220621, flown 2022-06-21,
// USDA-FSA-APFO), cut from the USGS National Map service
// https://imagery.nationalmap.gov/arcgis/rest/services/USGSNAIPImagery/ImageServer on 2026-11-12. Not Esri imagery.
const LOT = require("../../../assets/images/intro-lot.jpg");
const ASPECT = 800 / 1000;

type Rect = { x: number; y: number; w: number; h: number };
// Where the trucks are in that picture, as fractions of its width and height. The big lot east of the plaza has the
// shape of a corner (the hotel grounds cut into it): a wide part on top, a narrower one under its right half.
const LOT_TOP: Rect = { x: 0.618, y: 0.415, w: 0.367, h: 0.12 };
const LOT_LOW: Rect = { x: 0.745, y: 0.535, w: 0.24, h: 0.17 };
// The two rows by the store.
const ROWS: Rect[] = [
  { x: 0.452, y: 0.388, w: 0.16, h: 0.056 },
  { x: 0.393, y: 0.456, w: 0.084, h: 0.122 },
];
const WAY_IN = { x: 0.09, y: 0.385 }; // the access road where it leaves the street on the left
const LINE = 2;
const ARROW = 36;
/** Above this system font scale the card would hide too much of the picture, so it sits under it. */
export const CARD_BELOW_FONT_SCALE = 1.3;

/** Intro, screen 3 (Figma B3): the aerial picture with the truck parking and the way in, and the verdict on it. */
export function IntroLot() {
  const { colors } = useTheme();
  const tr = useT();
  const win = useWindowDimensions();
  // The card is two lines (the place, the verdict), each cut rather than wrapped, so on the picture it covers the
  // road at the bottom and nothing else. With a very large system font even two lines are too tall: then the card
  // goes under the picture. One rule, by the font scale, so the layout does not depend on measuring text.
  const cardBelow = win.fontScale > CARD_BELOW_FONT_SCALE;
  // The picture keeps its shape (the marks sit on real trucks): as wide as the button, right under the heading,
  // smaller only when the room under the heading is short. The spare room stays below it.
  const [room, setRoom] = useState<{ w: number; h: number } | null>(null);
  const [cardH, setCardH] = useState(0);
  const maxW = room?.w ?? win.width - 2 * spacing.xl;
  const maxH = room ? room.h - (cardBelow ? cardH + spacing.md : 0) : maxW / ASPECT;
  const w = Math.max(160, Math.floor(Math.min(maxW, maxH * ASPECT)));
  const h = Math.floor(w / ASPECT);
  const px = (r: Rect) => ({
    left: Math.round(r.x * w),
    top: Math.round(r.y * h),
    width: Math.round((r.x + r.w) * w) - Math.round(r.x * w),
    height: Math.round((r.y + r.h) * h) - Math.round(r.y * h),
  });
  const top = px(LOT_TOP);
  const low = px(LOT_LOW);
  const tint = [styles.abs, styles.tint, { backgroundColor: colors.success }];
  const outline = { borderColor: colors.success };
  // The bottom edge of the wide part, left of the narrow part under it.
  const edge = { left: top.left, top: top.top + top.height - LINE, width: low.left - top.left + LINE, height: LINE, backgroundColor: colors.success };
  const arrowTop = Math.round(WAY_IN.y * h) - ARROW / 2;
  const tag = [styles.tag, shadow(colors, 1), { backgroundColor: colors.surface }];

  const card = (
    <View
      testID="intro-lot-card"
      accessible
      onLayout={cardBelow ? (e) => setCardH(e.nativeEvent.layout.height) : undefined}
      style={[styles.card, cardBelow ? [styles.cardBelow, { width: w, backgroundColor: colors.surfaceSecondary }] : [styles.cardOn, shadow(colors, 2), { backgroundColor: colors.surface }]]}
    >
      <Image source={require("../../../assets/images/avatar.png")} style={[styles.avatar, { backgroundColor: colors.avatarBg }]} />
      <View style={styles.flex}>
        <Text numberOfLines={1} style={[styles.place, { color: colors.onSurface }]}>
          Sacramento 49er Travel Plaza
        </Text>
        {/* The green is a dot: as text on white it is 2.2:1. */}
        <View style={styles.verdict}>
          <View importantForAccessibility="no" style={[styles.dot, { backgroundColor: colors.success }]} />
          <Text numberOfLines={1} style={[styles.verdictText, { color: colors.onSurface }]}>
            Likely fits your truck
          </Text>
        </View>
      </View>
    </View>
  );

  return (
    <View style={styles.root}>
      <Text accessibilityRole="header" style={[introStyles.title, { color: colors.onSurface }]}>
        I check the lot first
      </Text>
      <View style={styles.room} onLayout={(e) => setRoom({ w: e.nativeEvent.layout.width, h: e.nativeEvent.layout.height })}>
        <View testID="intro-lot-picture" style={[styles.box, { width: w, height: h, backgroundColor: colors.mapPlaceholder }]}>
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
            <View style={[tint, top]} />
            <View style={[tint, low]} />
            <View style={[styles.abs, styles.topLine, outline, top]} />
            <View style={[styles.abs, styles.lowLine, outline, low]} />
            <View style={[styles.abs, edge]} />
            {ROWS.map((r, i) => (
              <View key={i} style={[styles.abs, px(r)]}>
                <View style={[styles.fill, styles.tint, { backgroundColor: colors.success }]} />
                <View style={[styles.fill, styles.rowLine, outline]} />
              </View>
            ))}
            {/* Held by its bottom edge just above the area, so a larger font grows it upwards, never over the outline. */}
            <View testID="intro-lot-tag-parking" style={[tag, { right: w - top.left - top.width, bottom: h - top.top + spacing.xs }]}>
              <View style={[styles.tagDot, { backgroundColor: colors.success }]} />
              <Text style={[styles.tagText, { color: colors.onSurface }]}>Truck parking</Text>
            </View>
            <View style={[styles.arrow, { left: Math.round(WAY_IN.x * w) - ARROW / 2, top: arrowTop, backgroundColor: colors.info, borderColor: colors.onInfo }]}>
              <Icon name="arrow-right" size={22} color={colors.onInfo} />
            </View>
            {/* Held by its top edge under the arrow: it grows downwards, away from the arrow. */}
            <View testID="intro-lot-tag-way-in" style={[tag, { left: spacing.sm, top: arrowTop + ARROW + spacing.xs }]}>
              <View style={[styles.tagDot, { backgroundColor: colors.info }]} />
              <Text style={[styles.tagText, { color: colors.onSurface }]}>Way in</Text>
            </View>
          </View>
          {!cardBelow && card}
        </View>
        {cardBelow && card}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  // The same side margins as the button under it (IntroGate footer).
  root: { flex: 1, paddingHorizontal: spacing.xl, paddingBottom: spacing.lg },
  room: { flex: 1, alignItems: "center", justifyContent: "flex-start", marginTop: spacing.lg },
  box: { borderRadius: radius.lg + spacing.sm, overflow: "hidden" },
  fill: { position: "absolute", left: 0, top: 0, width: "100%", height: "100%" },
  abs: { position: "absolute" },
  tint: { opacity: 0.32 },
  topLine: { borderTopWidth: LINE, borderLeftWidth: LINE, borderRightWidth: LINE },
  lowLine: { borderBottomWidth: LINE, borderLeftWidth: LINE, borderRightWidth: LINE },
  rowLine: { borderWidth: LINE },
  tag: { position: "absolute", flexDirection: "row", alignItems: "center", gap: 6, borderRadius: radius.pill, paddingHorizontal: 10, paddingVertical: 5 },
  tagDot: { width: 8, height: 8, borderRadius: 4 },
  tagText: { fontFamily: fonts.extrabold, fontSize: 13 },
  arrow: { position: "absolute", width: ARROW, height: ARROW, borderRadius: ARROW / 2, borderWidth: 2, alignItems: "center", justifyContent: "center" },
  card: { flexDirection: "row", alignItems: "center", gap: spacing.md, borderRadius: radius.lg, padding: spacing.md },
  cardOn: { position: "absolute", left: spacing.md, right: spacing.md, bottom: spacing.md },
  cardBelow: { marginTop: spacing.md },
  avatar: { width: 52, height: 52, borderRadius: 26 },
  flex: { flex: 1, minWidth: 0 },
  place: { fontFamily: fonts.bold, fontSize: 18, lineHeight: 23, flexShrink: 1 },
  verdict: { flexDirection: "row", alignItems: "center", gap: spacing.sm, marginTop: 2 },
  dot: { width: 11, height: 11, borderRadius: 6 },
  verdictText: { flex: 1, fontFamily: fonts.bold, fontSize: 16, lineHeight: 22 },
});
