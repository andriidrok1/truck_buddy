import { useMemo, useState } from "react";
import { Linking, Platform, Pressable, ScrollView, StyleSheet, Text, useWindowDimensions, View } from "react-native";
import Animated, { FadeInDown } from "react-native-reanimated";

import { fonts, radius, spacing, useTheme, type ThemeColors } from "../theme";
import type { CallCard, PriceFact, Provider, ProvidersResult } from "../types";
import { Icon } from "./Icon";
import { PressableScale } from "./PressableScale";

type Filter = "open" | "mobile" | "247" | "prices" | "truck";
type Sort = "best" | "nearest" | "cheapest" | "fastest";

const FILTERS: { key: Filter; label: string; test: (p: Provider) => boolean }[] = [
  { key: "open", label: "Open now", test: (p) => p.open_now === true },
  { key: "mobile", label: "Mobile service", test: (p) => p.facts.mobile_service === true },
  { key: "247", label: "24/7", test: (p) => p.listed_24_7 || p.facts.hours_24_7 === true },
  { key: "prices", label: "Has prices", test: (p) => p.facts.prices.length > 0 },
  { key: "truck", label: "Truck-only", test: (p) => p.facts.truck_focused },
];
const SORTS: { key: Sort; label: string }[] = [
  { key: "best", label: "Best match" },
  { key: "nearest", label: "Nearest" },
  { key: "cheapest", label: "Cheapest" },
  { key: "fastest", label: "Fastest ETA" },
];

const minPrice = (p: Provider) => Math.min(...p.facts.prices.map((x) => x.amount_usd_low), Infinity);
const eta = (p: Provider) => p.facts.wait_time?.typical_min_high ?? Infinity;
const money = (x: PriceFact) =>
  x.amount_usd_low === x.amount_usd_high ? `$${x.amount_usd_low}` : `$${x.amount_usd_low}–${x.amount_usd_high}`;
const priceLine = (x: PriceFact) => `${x.source === "review" ? "drivers" : "website"}: ${money(x)} ${x.what}`;

export const dial = (phone?: string | null) => {
  if (phone) Linking.openURL(`tel:${phone.replace(/[^\d+]/g, "")}`).catch(() => {});
};

export function shareLocation(card?: CallCard | null) {
  if (!card) return;
  const sep = Platform.OS === "ios" ? "&" : "?";
  Linking.openURL(`sms:${sep}body=${encodeURIComponent(card.share_text)}`).catch(() => {});
}

function Chip({ label, on, onPress, testID, colors }: { label: string; on: boolean; onPress: () => void; testID: string; colors: ThemeColors }) {
  return (
    <Pressable
      testID={testID}
      onPress={onPress}
      hitSlop={{ top: 6, bottom: 6 }}
      style={[styles.chip, { backgroundColor: on ? colors.brandPrimary : colors.surface, borderColor: on ? colors.brandPrimary : colors.border }]}
    >
      <Text style={[styles.chipText, { color: on ? colors.onBrandPrimary : colors.onSurface }]}>{label}</Text>
    </Pressable>
  );
}

function Badge({ label, color, colors }: { label: string; color: string; colors: ThemeColors }) {
  return (
    <View style={[styles.badge, { borderColor: color }]}>
      <Text style={[styles.badgeText, { color: color === colors.border ? colors.onSurfaceSecondary : color }]}>{label}</Text>
    </View>
  );
}

function TellThem({ card }: { card: CallCard }) {
  const { colors } = useTheme();
  const [open, setOpen] = useState(false);
  const rows = [
    ["Problem", card.problem],
    ...(card.tire ? [["Tire", card.tire]] : []),
    ["Where", card.location ? `${card.location} (GPS ${card.gps})` : `GPS ${card.gps}`],
    ["Truck", card.truck],
  ];
  return (
    <Pressable
      testID="tell-them-card"
      onPress={() => setOpen((o) => !o)}
      style={[styles.tell, { backgroundColor: colors.surfaceSecondary }]}
    >
      <View style={styles.rowBetween}>
        <View style={styles.row}>
          <Icon name="phone-message-outline" size={18} color={colors.onSurface} />
          <Text style={[styles.tellTitle, { color: colors.onSurface }]}>What to tell them when you call</Text>
        </View>
        <Icon name={open ? "chevron-up" : "chevron-down"} size={20} color={colors.muted} />
      </View>
      {open && (
        <View testID="tell-them-details" style={styles.tellBody}>
          {rows.map(([k, v]) => (
            <Text key={k} style={[styles.small, { color: colors.onSurfaceSecondary }]}>
              <Text style={{ fontFamily: fonts.semibold, color: colors.onSurface }}>{k}: </Text>
              {v}
            </Text>
          ))}
          <Text style={[styles.small, { color: colors.onSurface, fontFamily: fonts.semibold, marginTop: spacing.xs }]}>Ask:</Text>
          {card.questions.map((q) => (
            <Text key={q} style={[styles.small, { color: colors.onSurfaceSecondary }]}>• {q}</Text>
          ))}
        </View>
      )}
    </Pressable>
  );
}

function ProviderCard({ p, index, onRoute }: { p: Provider; index: number; onRoute: (p: Provider) => void }) {
  const { colors } = useTheme();
  const [open, setOpen] = useState(false);
  const f = p.facts;
  const price = f.prices[0];
  const quote = p.quotes[0];
  return (
    <Animated.View entering={FadeInDown.delay(index * 40).duration(200)}>
      <Pressable
        testID={`provider-card-${p.id}`}
        onPress={() => setOpen((o) => !o)}
        style={[styles.card, { backgroundColor: colors.surfaceSecondary }]}
      >
        <View style={styles.rowBetween}>
          <Text testID={`provider-name-${p.id}`} numberOfLines={1} style={[styles.name, { color: colors.onSurface }]}>
            {p.name}
          </Text>
          <Icon name={open ? "chevron-up" : "chevron-down"} size={20} color={colors.muted} />
        </View>
        <View style={styles.row}>
          {p.distance_mi != null && <Text style={[styles.meta, { color: colors.onSurfaceSecondary }]}>{p.distance_mi} mi</Text>}
          {p.rating != null && (
            <>
              <Icon name="star" size={14} color={colors.warning} />
              <Text testID={`provider-rating-${p.id}`} style={[styles.meta, { color: colors.onSurface }]}>
                {p.rating.toFixed(1)} ({p.rating_count})
              </Text>
            </>
          )}
        </View>
        <View style={styles.badges}>
          {p.open_now === true && <Badge label="Open now" color={colors.success} colors={colors} />}
          {f.mobile_service === true && <Badge label="Mobile" color={colors.info} colors={colors} />}
          {(p.listed_24_7 || f.hours_24_7 === true) && <Badge label="24/7" color={colors.info} colors={colors} />}
          {f.truck_focused && <Badge label="Trucks" color={colors.border} colors={colors} />}
          {p.shop_only && <Badge label="Shop only" color={colors.warning} colors={colors} />}
        </View>
        {price && (
          <Text testID={`provider-price-${p.id}`} style={[styles.line, { color: colors.onSurface }]}>
            {priceLine(price)}
          </Text>
        )}
        {f.wait_time && (
          <Text testID={`provider-eta-${p.id}`} style={[styles.line, { color: colors.onSurface }]}>
            out within {f.wait_time.typical_min_low > 0 ? `${f.wait_time.typical_min_low}–` : ""}
            {f.wait_time.typical_min_high} min {f.wait_time.source === "website" ? "per website" : "by reviews"}
          </Text>
        )}
        {quote && (
          <Text numberOfLines={open ? undefined : 2} style={[styles.quote, { color: colors.onSurfaceSecondary }]}>
            “{quote}”
          </Text>
        )}
        {f.red_flags.length > 0 && (
          <Text testID={`provider-redflag-${p.id}`} style={[styles.line, { color: colors.error }]}>
            ⚠ {f.red_flags.join(" · ")}
          </Text>
        )}

        {open && (
          <View testID={`provider-details-${p.id}`} style={styles.details}>
            {f.prices.length === 0 && <Text style={[styles.small, { color: colors.muted }]}>No prices posted — ask when you call.</Text>}
            {f.prices.map((x, i) => (
              <Text key={i} style={[styles.small, { color: colors.onSurfaceSecondary }]}>
                <Text style={{ fontFamily: fonts.semibold, color: colors.onSurface }}>{money(x)} {x.what}</Text> ({x.source}): “{x.quote}”
              </Text>
            ))}
            {f.payment.length > 0 && (
              <Text style={[styles.small, { color: colors.onSurfaceSecondary }]}>Payment: {f.payment.join(", ")}</Text>
            )}
            {p.quotes.slice(1, 2).map((q) => (
              <Text key={q} style={[styles.quote, { color: colors.onSurfaceSecondary }]}>“{q}”</Text>
            ))}
            {p.website && (
              <Text
                testID={`provider-website-${p.id}`}
                onPress={() => Linking.openURL(p.website!).catch(() => {})}
                style={[styles.small, { color: colors.info }]}
              >
                {p.website.replace(/^https?:\/\//, "").replace(/\/$/, "")}
              </Text>
            )}
          </View>
        )}

        <View style={styles.btnRow}>
          <PressableScale
            testID={`provider-call-button-${p.id}`}
            onPress={() => dial(p.phone)}
            containerStyle={styles.btnItem}
            style={[styles.btn, { backgroundColor: colors.brandPrimary, borderColor: colors.brandPrimary }]}
          >
            <Icon name="phone" size={18} color={colors.onBrandPrimary} />
            <Text style={[styles.btnText, { color: colors.onBrandPrimary }]}>Call</Text>
          </PressableScale>
          <PressableScale
            testID={`provider-route-button-${p.id}`}
            onPress={() => onRoute(p)}
            containerStyle={styles.btnItem}
            style={[styles.btn, { backgroundColor: colors.surface, borderColor: colors.border }]}
          >
            <Icon name="directions" size={18} color={colors.onSurface} />
            <Text style={[styles.btnText, { color: colors.onSurface }]}>Route</Text>
          </PressableScale>
        </View>
      </Pressable>
    </Animated.View>
  );
}

/** Breakdown research result: filters, sort, provider cards, "what to tell them", share location. */
export function ProvidersList({ data, onRoute }: { data: ProvidersResult; onRoute: (p: Provider) => void }) {
  const { colors } = useTheme();
  const { height } = useWindowDimensions();
  const [filters, setFilters] = useState<Filter[]>([]);
  const [sort, setSort] = useState<Sort>("best");

  const list = useMemo(() => {
    const kept = data.providers.filter((p) => filters.every((k) => FILTERS.find((f) => f.key === k)!.test(p)));
    if (sort === "nearest") return [...kept].sort((a, b) => (a.distance_mi ?? 0) - (b.distance_mi ?? 0));
    if (sort === "cheapest") return [...kept].sort((a, b) => minPrice(a) - minPrice(b));
    if (sort === "fastest") return [...kept].sort((a, b) => eta(a) - eta(b));
    return kept; // backend order = best match
  }, [data.providers, filters, sort]);

  const toggle = (k: Filter) => setFilters((fs) => (fs.includes(k) ? fs.filter((x) => x !== k) : [...fs, k]));

  return (
    <View testID="providers-list">
      <ScrollView horizontal showsHorizontalScrollIndicator={false} style={styles.chipRow} contentContainerStyle={styles.chipRowContent}>
        {FILTERS.map((f) => (
          <Chip key={f.key} testID={`provider-filter-${f.key}`} label={f.label} on={filters.includes(f.key)} onPress={() => toggle(f.key)} colors={colors} />
        ))}
      </ScrollView>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} style={styles.chipRow} contentContainerStyle={styles.chipRowContent}>
        {SORTS.map((s) => (
          <Chip key={s.key} testID={`provider-sort-${s.key}`} label={s.label} on={sort === s.key} onPress={() => setSort(s.key)} colors={colors} />
        ))}
      </ScrollView>

      <ScrollView style={{ maxHeight: height * 0.42 }} contentContainerStyle={styles.list} showsVerticalScrollIndicator={false}>
        {data.summary && (
          <Text testID="providers-summary" numberOfLines={3} style={[styles.summary, { color: colors.onSurface }]}>{data.summary}</Text>
        )}
        {data.call_card && <TellThem card={data.call_card} />}
        {list.length === 0 && (
          <Text testID="providers-empty" style={[styles.small, { color: colors.muted }]}>Nobody matches these filters.</Text>
        )}
        {list.map((p, i) => (
          <ProviderCard key={p.id} p={p} index={i} onRoute={onRoute} />
        ))}
        {data.call_card && (
          <PressableScale
            testID="share-location-button"
            onPress={() => shareLocation(data.call_card)}
            style={[styles.btn, { backgroundColor: colors.surface, borderColor: colors.border }]}
          >
            <Icon name="message-text-outline" size={18} color={colors.onSurface} />
            <Text style={[styles.btnText, { color: colors.onSurface }]}>Share my location</Text>
          </PressableScale>
        )}
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  chipRow: { height: 48, flexGrow: 0 },
  chipRowContent: { gap: spacing.sm, alignItems: "center", paddingRight: spacing.lg },
  chip: { height: 36, flexShrink: 0, borderRadius: radius.pill, borderWidth: 1, paddingHorizontal: spacing.md, justifyContent: "center" },
  chipText: { fontFamily: fonts.semibold, fontSize: 13 },
  list: { gap: spacing.sm, paddingBottom: spacing.xs, paddingTop: spacing.xs },
  summary: { fontFamily: fonts.medium, fontSize: 14, lineHeight: 20 },
  tell: { borderRadius: radius.lg, padding: spacing.md },
  tellTitle: { fontFamily: fonts.bold, fontSize: 14 },
  tellBody: { marginTop: spacing.sm, gap: 2 },
  card: { borderRadius: radius.lg, padding: spacing.md, gap: spacing.xs },
  row: { flexDirection: "row", alignItems: "center", gap: 6 },
  rowBetween: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: spacing.sm },
  name: { fontFamily: fonts.bold, fontSize: 16, flex: 1 },
  meta: { fontFamily: fonts.medium, fontSize: 13 },
  badges: { flexDirection: "row", flexWrap: "wrap", gap: 6 },
  badge: { borderWidth: 1, borderRadius: radius.pill, paddingHorizontal: spacing.sm, paddingVertical: 2 },
  badgeText: { fontFamily: fonts.semibold, fontSize: 11 },
  line: { fontFamily: fonts.semibold, fontSize: 13 },
  quote: { fontFamily: fonts.regular, fontSize: 13, fontStyle: "italic", lineHeight: 18 },
  small: { fontFamily: fonts.regular, fontSize: 13, lineHeight: 18 },
  details: { gap: 4, marginTop: spacing.xs },
  btnRow: { flexDirection: "row", gap: spacing.sm, marginTop: spacing.sm },
  btnItem: { flex: 1 },
  btn: { height: 48, borderRadius: radius.md, borderWidth: 1, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: spacing.sm },
  btnText: { fontFamily: fonts.bold, fontSize: 14 },
});
