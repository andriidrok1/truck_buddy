// Live truck-parking badge text, pure so it can be unit-tested without React.
import { getLang, t, type Lang } from "../i18n";
import type { ParkingTrend, Place } from "../types";
import { clockTime } from "../utils/arrivalClock";

/** A count older than this is shown greyed with its clock time instead of as live. */
export const STALE_AFTER_MS = 30 * 60 * 1000;

const TREND_WORD: Record<ParkingTrend, string> = { FILLING: "filling", CLEARING: "clearing", STEADY: "steady" };

/** MaterialDesignIcons name for a trend. */
export const TREND_ICON: Record<ParkingTrend, string> = {
  FILLING: "trending-down", // spaces going away
  CLEARING: "trending-up",
  STEADY: "trending-neutral",
};

export type ParkingBadge = {
  /** "56/68 open · clearing", "almost full", "68 spots" */
  text: string;
  /** "low" when the lot is almost full, "stale" when the count is older than STALE_AFTER_MS. */
  tone: "live" | "low" | "stale";
  trend: ParkingTrend | null;
  /** Set when stale: "as of 7:42 pm" / "на 19:42". */
  asOf: string | null;
};

const hasAny = (p: Place) =>
  p.parking_available != null || p.parking_capacity != null || p.parking_low != null || !!p.parking_trend;

/** Only truck stops and rest areas carry live counts; everything else has no badge. */
export const showsParkingBadge = (p: Place) => (p.category === "truck_stop" || p.category === "rest_area") && hasAny(p);

function clock(iso: string, lang: Lang): string | null {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return clockTime(d, lang).toLowerCase();
}

/**
 * Badge for a place's live parking fields, or null when there is nothing to show.
 * `now` is injectable for tests.
 */
export function parkingBadgeText(place: Place, now: number = Date.now(), lang: Lang = getLang()): ParkingBadge | null {
  if (!hasAny(place)) return null;
  const avail = place.parking_available ?? null;
  const cap = place.parking_capacity ?? null;
  const trend = place.parking_trend ?? null;

  let text: string;
  if (place.parking_low && avail == null) text = "almost full";
  else if (avail != null && cap != null) text = `${avail}/${cap} open`;
  else if (avail != null) text = `${avail} open`;
  else if (cap != null) text = `${cap} spots`;
  else if (trend) text = TREND_WORD[trend];
  else return null;

  if (place.parking_low && avail != null) text += " · almost full";
  else if (trend && avail != null) text += ` · ${TREND_WORD[trend]}`;

  let tone: ParkingBadge["tone"] = place.parking_low ? "low" : "live";
  let asOf: string | null = null;
  if (place.parking_updated) {
    const at = new Date(place.parking_updated).getTime();
    if (!Number.isNaN(at) && now - at > STALE_AFTER_MS) {
      tone = "stale";
      asOf = clock(place.parking_updated, lang);
      if (asOf) asOf = t("as of {time}", { time: asOf }, lang);
    }
  }
  // "12/40 open · almost full" fact by fact in Russian (src/i18n/ru.ts), English as it was (QA 2026-11-09, S9).
  return { text: t(text, undefined, lang), tone, trend, asOf };
}
