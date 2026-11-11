// Picking between the routes the engine offered (`RouteInfo.alternatives`). Pure: the map screen
// swaps `active.route` locally, nothing goes to the backend.

import { getLang, t, type Lang } from "../i18n";
import type { RouteInfo } from "../types";
import { durationLabel } from "../utils/duration";

/** "I-80 / US-50" from the engine's labels, or "Alternative N" (1-based) without them. */
export function alternativeLabel(route: RouteInfo, i: number): string {
  return route.labels?.length ? route.labels.join(" / ") : `Alternative ${i + 1}`;
}

/** "Routes (3)": the primary plus its alternatives; null when there is nothing to choose from. */
export function routesChipText(route: RouteInfo): string | null {
  const n = route.alternatives?.length ?? 0;
  return n ? `Routes (${n + 1})` : null;
}

/** What the copilot says after a swap: "Switched to the I-80 route." / "Switched to alternative 2." */
export function switchedLine(route: RouteInfo, i: number, lang: Lang = getLang()): string {
  // Each label on its own: "Fastest ETA and Cheapest" as one name stayed English in Russian (QA 2026-11-09, S15).
  if (!route.labels?.length) return t("Switched to alternative {n}.", { n: i + 1 }, lang);
  const name = route.labels.map((l) => t(l, undefined, lang)).join(` ${t("and", undefined, lang)} `);
  return t("Switched to the {name} route.", { name }, lang);
}

/**
 * Make alternative `i` the primary. Its `alternatives` become the remaining ones plus the previous
 * primary (last), so the driver can come back. Out of range: the primary comes back unchanged.
 */
export function swapAlternative(primary: RouteInfo, i: number): { route: RouteInfo; alternatives: RouteInfo[] } {
  const alts = primary.alternatives ?? [];
  const pick = alts[i];
  if (!pick) return { route: primary, alternatives: alts };
  const previous: RouteInfo = { ...primary };
  delete previous.alternatives; // no nesting: the list lives on the primary only
  const alternatives = [...alts.filter((_, j) => j !== i), previous];
  return { route: { ...pick, alternatives }, alternatives };
}

/** "Cheapest · 8 min · 4.2 mi · $12.50 tolls · ~$170": the tag, then the facts, then fuel + tolls at the regional
 *  diesel average (only when the server could price it). */
export function routeFacts(r: RouteInfo) {
  const parts = [durationLabel(r.duration_s), `${r.distance_mi.toFixed(r.distance_mi < 10 ? 1 : 0)} mi`];
  if (r.tolls_usd) parts.push(`$${r.tolls_usd.toFixed(2)} tolls`);
  if (r.cost_usd != null) parts.push(`~$${Math.round(r.cost_usd)}`);
  const tag = routeTag(r);
  return (tag ? [tag, ...parts] : parts).join(" · ");
}

/** "Cheapest" wins over "Fastest" when a route is both (there is then no cheaper one to point at). */
export function routeTag(r: RouteInfo): string | null {
  if (r.tags?.includes("cheapest")) return "Cheapest";
  if (r.tags?.includes("fastest")) return "Fastest";
  return null;
}
