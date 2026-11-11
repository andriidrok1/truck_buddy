import { ActivityIndicator, Pressable, StyleSheet, View, type LayoutChangeEvent } from "react-native";
import { Text } from "../i18n/Text";
import Animated, { FadeInDown } from "react-native-reanimated";

import { categoryColor, categoryIcon } from "../constants";
import { formatNumber, useLang, useT } from "../i18n";
import { fonts, radius, spacing, useTheme } from "../theme";
import type { ParkingState, Place } from "../types";
import { Icon } from "./Icon";
import { ParkingLiveBadge } from "./ParkingLiveBadge";
import { ParkingStatus } from "./ParkingStatus";
import { PressableScale } from "./PressableScale";
import { reviewsGistLine } from "./ReviewsGist";

type Props = {
  options: Place[];
  parking: Record<string, ParkingState>;
  onParking: (p: Place) => void;
  onRoute: (p: Place) => void;
  /** Layout of the "N found nearby" line (the sheet keeps it visible when peeked). */
  onTitleLayout?: (e: LayoutChangeEvent) => void;
};

function Rating({ place }: { place: Place }) {
  const { colors } = useTheme();
  const lang = useLang(); // "4,5" in Russian
  if (place.rating == null) return null; // "No rating yet" on every card was noise (UI review 2026-11-05)
  return (
    <View style={styles.ratingRow}>
      <Icon name="star" size={16} color={colors.warning} />
      <Text testID={`option-rating-${place.id}`} style={[styles.ratingText, { color: colors.onSurface }]}>
        {formatNumber(place.rating, lang, 1)}
      </Text>
      {place.rating_count != null && (
        <Text style={[styles.meta, { color: colors.muted }]}>({place.rating_count})</Text>
      )}
    </View>
  );
}

/** One place. The card itself is the route target (a tap anywhere routes there); only the best match, first in
 *  the list, carries the filled Route button, the others an outlined one, so the screen has one primary action
 *  instead of a black button on every card (UI review 2026-11-05). */
function OptionCard({ place, state, onParking, onRoute, index }: { place: Place; state?: ParkingState; onParking: () => void; onRoute: () => void; index: number }) {
  const { colors } = useTheme();
  const tr = useT();
  const primary = index === 0;
  const tint = categoryColor(colors, place.category);
  const description = place.summary || place.address;
  const gist = reviewsGistLine(place.reviews, place.rating);
  return (
    <Animated.View entering={FadeInDown.delay(index * 50).duration(220)}>
    <Pressable
      testID={`option-card-${place.id}`}
      accessibilityRole="button"
      accessibilityLabel={tr("Route to {name}", { name: place.name })}
      onPress={onRoute}
      style={({ pressed }) => [styles.card, { backgroundColor: colors.surfaceSecondary, opacity: pressed ? 0.85 : 1 }]}
    >
      <View style={styles.topRow}>
        <View style={[styles.catIcon, { backgroundColor: tint }]}>
          <Icon name={categoryIcon(place.category)} size={16} color={colors.onCategory} />
        </View>
        <View style={styles.titleCol}>
          <Text testID={`option-name-${place.id}`} numberOfLines={1} style={[styles.name, { color: colors.onSurface }]}>
            {place.name}
          </Text>
          <View style={styles.metaRow}>
            <Rating place={place} />
            {place.distance_mi != null && (
              <Text testID={`option-distance-${place.id}`} style={[styles.meta, { color: colors.onSurfaceSecondary }]}>
                {place.rating != null ? "· " : ""}
                {tr(`${place.distance_mi.toFixed(1)} mi`)}
              </Text>
            )}
            {place.diesel_price != null && (
              <Text testID={`option-diesel-${place.id}`} style={[styles.meta, { color: colors.onSurface }]}>
                <Text style={{ color: colors.muted }}>·</Text>
                {/* The dot is an element, so Text leaves the rest alone: translated here (QA 2026-11-09, S8). */}
                {` ${tr(place.diesel_kind === "auto" ? "Diesel {price} (car pumps)" : "Diesel {price}", { price: `$${place.diesel_price.toFixed(2)}` })}`}
              </Text>
            )}
          </View>
          {!!gist && (
            <Text testID={`option-review-gist-${place.id}`} numberOfLines={1} style={[styles.gist, { color: colors.onSurfaceSecondary }]}>
              {gist}
            </Text>
          )}
          <ParkingLiveBadge place={place} idSuffix={`-${place.id}`} />
        </View>
      </View>

      {!!description && (
        <Text testID={`option-description-${place.id}`} numberOfLines={2} style={[styles.desc, { color: colors.onSurfaceSecondary }]}>
          {description}
        </Text>
      )}

      {state?.result && <ParkingStatus parking={state.result} idSuffix={`-${place.id}`} place={place} />}

      <View style={styles.btnRow}>
        <PressableScale
          testID={`option-parking-button-${place.id}`}
          onPress={onParking}
          disabled={state?.loading}
          containerStyle={styles.btnItem}
          style={[styles.btn, { backgroundColor: colors.surface, borderColor: colors.borderStrong }]}
        >
          {state?.loading ? (
            <ActivityIndicator size="small" color={colors.onSurface} />
          ) : (
            <Icon name="satellite-variant" size={20} color={colors.onSurface} />
          )}
          <Text style={[styles.btnText, { color: colors.onSurface }]}>
            {state?.loading ? "Checking…" : state?.result ? "Parking checked" : "See parking"}
          </Text>
        </PressableScale>
        <PressableScale
          testID={`option-route-button-${place.id}`}
          onPress={onRoute}
          containerStyle={styles.btnItem}
          style={[styles.btn, primary ? { backgroundColor: colors.brandPrimary, borderColor: colors.brandPrimary } : { backgroundColor: colors.surface, borderColor: colors.borderStrong }]}
        >
          <Icon name="directions" size={20} color={primary ? colors.onBrandPrimary : colors.onSurface} />
          <Text style={[styles.btnText, { color: primary ? colors.onBrandPrimary : colors.onSurface }]}>Route</Text>
        </PressableScale>
      </View>
    </Pressable>
    </Animated.View>
  );
}

/** List of places found by the copilot; the sheet body it sits in does the scrolling. */
export function OptionsList({ options, parking, onParking, onRoute, onTitleLayout }: Props) {
  const { colors } = useTheme();
  return (
    <View testID="options-list">
      <Text testID="options-title" onLayout={onTitleLayout} style={[styles.title, { color: colors.onSurface }]}>
        {options.length === 1 ? "1 place nearby" : `${options.length} places nearby`}
      </Text>
      <View style={styles.list}>
        {options.map((p, i) => (
          <OptionCard key={p.id} index={i} place={p} state={parking[p.id]} onParking={() => onParking(p)} onRoute={() => onRoute(p)} />
        ))}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  title: { fontFamily: fonts.bold, fontSize: 18, marginBottom: spacing.sm },
  list: { gap: spacing.sm, paddingBottom: spacing.xs },
  card: { borderRadius: radius.lg, padding: spacing.md, gap: spacing.xs },
  topRow: { flexDirection: "row", alignItems: "center", gap: spacing.md },
  catIcon: { width: 32, height: 32, borderRadius: 16, alignItems: "center", justifyContent: "center" },
  titleCol: { flex: 1 },
  name: { fontFamily: fonts.bold, fontSize: 17 },
  metaRow: { flexDirection: "row", alignItems: "center", flexWrap: "wrap", gap: 4, marginTop: 2 },
  ratingRow: { flexDirection: "row", alignItems: "center", gap: 3 },
  ratingText: { fontFamily: fonts.semibold, fontSize: 14 },
  meta: { fontFamily: fonts.medium, fontSize: 13 },
  gist: { fontFamily: fonts.regular, fontSize: 12, marginTop: 2 },
  desc: { fontFamily: fonts.regular, fontSize: 13, lineHeight: 18 },
  btnRow: { flexDirection: "row", gap: spacing.sm, marginTop: spacing.sm },
  btnItem: { flex: 1 },
  btn: {
    height: 56,
    borderRadius: radius.md,
    borderWidth: 1,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: spacing.sm,
  },
  btnText: { fontFamily: fonts.bold, fontSize: 16 },
});
