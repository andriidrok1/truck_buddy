import { Linking, Pressable, StyleSheet, Text, View } from "react-native";

import { fonts, spacing, useTheme } from "../theme";
import type { Review } from "../types";
import { Icon } from "./Icon";

/** Opens a review on Google; a missing or odd URI is ignored instead of throwing. */
export function openReview(uri?: string | null) {
  if (!uri || !/^https?:\/\//i.test(uri)) return;
  Linking.openURL(uri).catch(() => {});
}

/** "4.1" or null; the gist line in the options list. */
export function reviewsGistLine(reviews: Review[] | undefined, rating?: number | null): string | null {
  const first = reviews?.find((r) => r.text?.trim());
  if (!first) return null;
  const stars = rating != null ? `★ ${rating.toFixed(1)} · ` : "";
  return `${stars}“${first.text.trim()}”`;
}

function Stars({ rating }: { rating?: number | null }) {
  const { colors } = useTheme();
  if (rating == null) return null;
  const full = Math.round(rating);
  return (
    <View style={styles.stars} accessibilityLabel={`${rating} stars`}>
      {[1, 2, 3, 4, 5].map((i) => (
        <Icon key={i} name={i <= full ? "star" : "star-outline"} size={12} color={colors.warning} />
      ))}
    </View>
  );
}

type Props = { reviews: Review[]; idSuffix?: string };

/**
 * Up to two Google reviews under the active place. Google's terms: the author stays visible and
 * every review links back to Google, hence "Reviews from Google" and the tap-through.
 */
export function ReviewsGist({ reviews, idSuffix = "" }: Props) {
  const { colors } = useTheme();
  const shown = reviews.filter((r) => r.text?.trim()).slice(0, 2);
  if (!shown.length) return null;
  const more = reviews[0]?.uri ?? shown[0]?.uri ?? null;

  return (
    <View testID={`reviews-gist${idSuffix}`} style={styles.wrap}>
      {shown.map((r, i) => (
        <Pressable
          key={`${r.author}-${i}`}
          testID={`review-${i}${idSuffix}`}
          accessibilityRole={r.uri ? "link" : undefined}
          accessibilityLabel={`Review by ${r.author}`}
          onPress={() => openReview(r.uri)}
          disabled={!r.uri}
          style={styles.review}
        >
          <View style={styles.head}>
            <Text testID={`review-author-${i}${idSuffix}`} numberOfLines={1} style={[styles.author, { color: colors.onSurface }]}>
              {r.author}
            </Text>
            <Stars rating={r.rating} />
          </View>
          <Text numberOfLines={2} style={[styles.text, { color: colors.onSurfaceSecondary }]}>
            {r.text.trim()}
          </Text>
        </Pressable>
      ))}
      <View style={styles.foot}>
        <Text testID={`reviews-attribution${idSuffix}`} style={[styles.attrib, { color: colors.onSurfaceTertiary }]}>
          Reviews from Google
        </Text>
        {!!more && (
          <Pressable testID={`reviews-more${idSuffix}`} accessibilityRole="link" accessibilityLabel="More reviews on Google" onPress={() => openReview(more)} hitSlop={8} style={styles.moreBtn}>
            <Text style={[styles.more, { color: colors.info }]}>More</Text>
            <Icon name="open-in-new" size={13} color={colors.info} />
          </Pressable>
        )}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { gap: spacing.xs, marginTop: spacing.xs },
  review: { gap: 2 },
  head: { flexDirection: "row", alignItems: "center", gap: 6 },
  author: { fontFamily: fonts.bold, fontSize: 13, flexShrink: 1 },
  stars: { flexDirection: "row", gap: 1 },
  text: { fontFamily: fonts.regular, fontSize: 13, lineHeight: 18 },
  foot: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  attrib: { fontFamily: fonts.regular, fontSize: 12, marginTop: 2 },
  moreBtn: { flexDirection: "row", alignItems: "center", gap: 3, minHeight: 28 },
  more: { fontFamily: fonts.semibold, fontSize: 12 },
});
