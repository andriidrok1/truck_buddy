import { useState } from "react";
import { Image, StyleSheet, useWindowDimensions, View } from "react-native";

import { useT } from "../../i18n";
import { Text } from "../../i18n/Text";
import { fonts, radius, shadow, spacing, useTheme } from "../../theme";
import { Icon } from "../Icon";
import { introStyles } from "./introStyles";

// 49er Travel Plaza (Sacramento) from above, 800x1000 px, about 390 m across (0.49 m per pixel, close to the 0.6 m the
// plane shot). Public domain: USDA NAIP aerial imagery (tile m_3812128_ne_10_060_20220621, flown 2022-06-21,
// USDA-FSA-APFO), cut from the USGS National Map service
// https://imagery.nationalmap.gov/arcgis/rest/services/USGSNAIPImagery/ImageServer on 2026-11-12. Not Esri imagery.
const LOT = require("../../../assets/images/intro-lot.jpg");
const ASPECT = 800 / 1000;

type Rect = { x: number; y: number; w: number; h: number };
// Where the trucks are in that picture, as fractions of its width and height. The big lot east of the plaza is cut
// by the hotel grounds on a slant: three steps, top to bottom, each ending at the same right edge and starting
// further right than the one above (the next starts where the one above ends).
export const LOT_STEPS: Rect[] = [
  { x: 0.592, y: 0.415, w: 0.393, h: 0.12 },
  { x: 0.745, y: 0.535, w: 0.24, h: 0.107 },
  { x: 0.83, y: 0.642, w: 0.155, h: 0.063 },
];
// The two rows by the store.
export const LOT_ROWS: Rect[] = [
  { x: 0.452, y: 0.388, w: 0.138, h: 0.056 },
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
  // The card is two lines (a short place name, the verdict), so on the picture it covers the road at the bottom and
  // nothing else. With a very large system font even two lines are too tall: then the card goes under the
  // picture. One rule, by the font scale, so the layout does not depend on measuring text.
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
  const steps = LOT_STEPS.map(px);
  const lotRight = steps[0].left + steps[0].width;
  const tint = [styles.abs, styles.tint, { backgroundColor: colors.success }];
  const line = { backgroundColor: colors.success };
  // The top of the highest marked area: the "Truck parking" tag stays above it.
  const marksTop = Math.min(...steps.map((s) => s.top), ...LOT_ROWS.map((r) => px(r).top));
  const arrowLeft = Math.round(WAY_IN.x * w) - ARROW / 2;
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
        {/* The short name: "Sacramento 49er Travel Plaza" needed a second line (or an ellipsis) on every phone. */}
        <Text numberOfLines={2} style={[styles.place, { color: colors.onSurface }]}>
          49er Travel Plaza
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
            {steps.map((s, i) => (
              <View key={`fill${i}`} style={[tint, s]} />
            ))}
            {/* The outline of the steps as one shape: both sides of each, the top of the first, the bottom of the
                last, and under each step the stretch left of the next one. */}
            {steps.map((s, i) => {
              const under = steps[i + 1];
              return (
                <View key={`line${i}`} style={[styles.abs, s]}>
                  <View style={[styles.abs, styles.sideLeft, line]} />
                  <View style={[styles.abs, styles.sideRight, line]} />
                  {i === 0 && <View style={[styles.abs, styles.sideTop, line]} />}
                  <View style={[styles.abs, styles.sideBottom, line, under && { right: undefined, width: under.left - s.left + LINE }]} />
                </View>
              );
            })}
            {LOT_ROWS.map((r, i) => (
              <View key={`row${i}`} style={[styles.abs, px(r)]}>
                <View style={[styles.fill, styles.tint, { backgroundColor: colors.success }]} />
                <View style={[styles.fill, styles.rowLine, { borderColor: colors.success }]} />
              </View>
            ))}
            {/* Above the highest marked area, held by its bottom and right edges: a larger font or a longer text
                (Russian) grows it up and to the left, over the road and the yard, never over a marked area. It
                stops short of the arrow and wraps inside itself from there. */}
            <View
              testID="intro-lot-tag-parking"
              style={[tag, { right: w - lotRight, bottom: h - marksTop + spacing.xs, maxWidth: lotRight - (arrowLeft + ARROW + spacing.sm) }]}
            >
              <View style={[styles.tagDot, { backgroundColor: colors.success }]} />
              <Text style={[styles.tagText, { color: colors.onSurface }]}>Truck parking</Text>
            </View>
            <View style={[styles.arrow, { left: arrowLeft, top: arrowTop, backgroundColor: colors.info, borderColor: colors.onInfo }]}>
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
  sideLeft: { left: 0, top: 0, bottom: 0, width: LINE },
  sideRight: { right: 0, top: 0, bottom: 0, width: LINE },
  sideTop: { left: 0, right: 0, top: 0, height: LINE },
  sideBottom: { left: 0, right: 0, bottom: 0, height: LINE },
  rowLine: { borderWidth: LINE },
  tag: { position: "absolute", flexDirection: "row", alignItems: "center", gap: 6, borderRadius: radius.pill, paddingHorizontal: 10, paddingVertical: 5 },
  tagDot: { width: 8, height: 8, borderRadius: 4 },
  tagText: { fontFamily: fonts.extrabold, fontSize: 13, flexShrink: 1 },
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
