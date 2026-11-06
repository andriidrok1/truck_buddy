// The spoken lines about a destination's parking (satellite check): moved verbatim from app/index.tsx.
import type { ParkingCheck } from "../types";

export const APPROACH_TIPS_M = 800; // about half a mile: the entry tip is useful here, not six miles out
/** One short line about the lot right after the answer; "" when there is nothing certain to say. */
export const statusLine = (pk: ParkingCheck) => {
  if (pk.verdict === "likely" || pk.verdict === "listed") return "Parking looks good there.";
  return "";
};
/** The entry and parking tips as one spoken line, for the approach and for "how do I get in". */
export const tipsLine = (pk: ParkingCheck) => {
  const entry = pk.entry_tip && pk.relative ? `The truck entrance is ${pk.entry_tip.charAt(0).toLowerCase()}${pk.entry_tip.slice(1)}` : pk.entry_tip;
  const tips = [entry, pk.park_tip].filter(Boolean).join(". ");
  return tips ? `${tips}.` : "";
};
/** " There's a lot with trailers to the southeast, looks like a private yard." (or "") */
export const nearbyLine = (pk: ParkingCheck) => {
  if (!pk.nearby) return "";
  const look = pk.nearby.kind === "unclear" ? "" : `, looks like a ${pk.nearby.kind}`;
  return ` There's a lot with trailers to the ${pk.nearby.direction}${look}.`;
};
