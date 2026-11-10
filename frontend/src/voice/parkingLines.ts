// The spoken lines about a destination's parking (satellite check), in the driver's language: the server sends the
// tips already translated, the frame around them is built here from whole templates (QA 2026-11-09, S2).
import { t } from "../i18n";
import type { ParkingCheck } from "../types";

export const APPROACH_TIPS_M = 800; // about half a mile: the entry tip is useful here, not six miles out
/** One short line about the lot right after the answer; "" when there is nothing certain to say. */
export const statusLine = (pk: ParkingCheck) => {
  if (pk.verdict === "likely" || pk.verdict === "listed") return t("Parking looks good there.");
  return "";
};
/** The verdict said right after the answer: a warning for a tight lot (with a lot nearby, if any), else statusLine. */
export const verdictLine = (pk: ParkingCheck) =>
  pk.verdict === "unlikely" ? `${t("Heads up: from the satellite, parking there looks tight for a rig.")}${nearbyLine(pk)}` : statusLine(pk);
/** The entry and parking tips as one spoken line, for the approach and for "how do I get in". */
export const tipsLine = (pk: ParkingCheck) => {
  const entry =
    pk.entry_tip && pk.relative ? t("The truck entrance is {tip}", { tip: `${pk.entry_tip.charAt(0).toLowerCase()}${pk.entry_tip.slice(1)}` }) : pk.entry_tip;
  const tips = [entry, pk.park_tip].filter(Boolean).join(". ");
  return tips ? `${tips}.` : "";
};
/** " There's a lot with trailers to the southeast, looks like a private yard." (or "") */
export const nearbyLine = (pk: ParkingCheck) => {
  if (!pk.nearby) return "";
  const where = t(`to the ${pk.nearby.direction}`);
  if (pk.nearby.kind === "unclear") return ` ${t("There's a lot with trailers {where}.", { where })}`;
  return ` ${t("There's a lot with trailers {where}, looks like a {kind}.", { where, kind: t(pk.nearby.kind) })}`;
};
