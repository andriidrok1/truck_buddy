import { ActivityIndicator, ScrollView, StyleSheet, Text, useWindowDimensions, View } from "react-native";
import Animated, { FadeInDown } from "react-native-reanimated";

import { categoryColor, categoryIcon } from "../constants";
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
};

function Rating({ place }: { place: Place }) {
  const { colors } = useTheme();
  if (place.rating == null) {
    return <Text testID={`option-rating-${place.id}`} style={[styles.meta, { color: colors.muted }]}>No rating yet</Text>;
  }
  return (
    <View style={styles.ratingRow}>
      <Icon name="star" size={16} color={colors.warning} />
      <Text testID={`option-rating-${place.id}`} style={[styles.ratingText, { color: colors.onSurface }]}>
        {place.rating.toFixed(1)}
      </Text>
      {place.rating_count != null && (
        <Text style={[styles.meta, { color: colors.muted }]}>({place.rating_count})</Text>
      )}
    </View>
  );
}

function OptionCard({ place, state, onParking, onRoute, index }: { place: Place; state?: ParkingState; onParking: () => void; onRoute: () => void; index: number }) {
  const { colors } = useTheme();
  const tint = categoryColor(colors, place.category);
  const description = place.summary || place.address;
  const gist = reviewsGistLine(place.reviews, place.rating);
  return (
    <Animated.View
      entering={FadeInDown.delay(index * 50).duration(220)}
      testID={`option-card-${place.id}`}
      style={[styles.card, { backgroundColor: colors.surfaceSecondary }]}
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
              <Text style={[styles.meta, { color: colors.onSurfaceSecondary }]}>· {place.distance_mi.toFixed(1)} mi</Text>
            )}
            {place.diesel_price != null && (
              <Text testID={`option-diesel-${place.id}`} style={[styles.meta, { color: colors.onSurface }]}>
                · Diesel ${place.diesel_price.toFixed(2)}{place.diesel_kind === "auto" ? " (car pumps)" : ""}
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
          style={[styles.btn, { backgroundColor: colors.surface, borderColor: colors.border }]}
        >
          {state?.loading ? (
            <ActivityIndicator size="small" color={colors.onSurface} />
          ) : (
            <Icon name="satellite-variant" size={18} color={colors.onSurface} />
          )}
          <Text style={[styles.btnText, { color: colors.onSurface }]}>
            {state?.loading ? "Checking…" : state?.result ? "Parking checked" : "See parking"}
          </Text>
        </PressableScale>
        <PressableScale
          testID={`option-route-button-${place.id}`}
          onPress={onRoute}
          containerStyle={styles.btnItem}
          style={[styles.btn, { backgroundColor: colors.brandPrimary, borderColor: colors.brandPrimary }]}
        >
          <Icon name="directions" size={18} color={colors.onBrandPrimary} />
          <Text style={[styles.btnText, { color: colors.onBrandPrimary }]}>Route</Text>
        </PressableScale>
      </View>
    </Animated.View>
  );
}

/** Scrollable list of places found by the copilot. */
export function OptionsList({ options, parking, onParking, onRoute }: Props) {
  const { colors } = useTheme();
  const { height } = useWindowDimensions();
  return (
    <View testID="options-list">
      <Text testID="options-title" style={[styles.title, { color: colors.onSurface }]}>
        {options.length} found nearby
      </Text>
      <ScrollView style={{ maxHeight: height * 0.45 }} contentContainerStyle={styles.list} showsVerticalScrollIndicator={false}>
        {options.map((p, i) => (
          <OptionCard key={p.id} index={i} place={p} state={parking[p.id]} onParking={() => onParking(p)} onRoute={() => onRoute(p)} />
        ))}
      </ScrollView>
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
  name: { fontFamily: fonts.bold, fontSize: 16 },
  metaRow: { flexDirection: "row", alignItems: "center", gap: 6, marginTop: 2 },
  ratingRow: { flexDirection: "row", alignItems: "center", gap: 3 },
  ratingText: { fontFamily: fonts.semibold, fontSize: 14 },
  meta: { fontFamily: fonts.medium, fontSize: 13 },
  gist: { fontFamily: fonts.regular, fontSize: 12, marginTop: 2 },
  desc: { fontFamily: fonts.regular, fontSize: 13, lineHeight: 18 },
  btnRow: { flexDirection: "row", gap: spacing.sm, marginTop: spacing.sm },
  btnItem: { flex: 1 },
  btn: {
    height: 48,
    borderRadius: radius.md,
    borderWidth: 1,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: spacing.sm,
  },
  btnText: { fontFamily: fonts.bold, fontSize: 14 },
});
