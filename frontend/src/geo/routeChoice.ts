// Picking between the routes the engine offered (`RouteInfo.alternatives`). Pure: the map screen
// swaps `active.route` locally, nothing goes to the backend.

import type { RouteInfo } from "../types";

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
export function switchedLine(route: RouteInfo, i: number): string {
  return route.labels?.length ? `Switched to the ${route.labels.join(" and ")} route.` : `Switched to alternative ${i + 1}.`;
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
