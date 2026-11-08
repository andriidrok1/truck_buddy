import { TRUCK_LIMITS } from "../truckLimits.generated";

/** What the driver typed in the truck forms (onboarding and the truck editor). */
export type TruckInputs = { feet: string; inches: string; trailer: string; weight: string };
export type TruckField = "height" | "trailer" | "weight";

const toInt = (s: string) => parseInt(s.replace(/[^0-9]/g, ""), 10);
const ftIn = (inches: number) => (inches % 12 ? `${Math.floor(inches / 12)} ft ${inches % 12} in` : `${inches / 12} ft`);
const lb = (n: number) => `${n.toLocaleString("en-US")} lb`;

/**
 * One message per field that is out of range, with that field's limits; the limits are the server's
 * (src/truckLimits.generated.ts). Onboarding showed one line for all three fields and the driver had to guess
 * which one was wrong (QA 2026-11-02).
 */
export function truckErrors(t: TruckInputs): Partial<Record<TruckField, string>> {
  const out: Partial<Record<TruckField, string>> = {};
  const [hLo, hHi] = TRUCK_LIMITS.height_in;
  const inches = t.inches.trim() ? toInt(t.inches) : 0;
  const height = toInt(t.feet) * 12 + inches;
  if (!Number.isFinite(height) || !(inches >= 0 && inches <= 11) || height < hLo || height > hHi) {
    out.height = `Height must be ${ftIn(hLo)} to ${ftIn(hHi)}.`;
  }
  const [tLo, tHi] = TRUCK_LIMITS.trailer_ft;
  const trailer = toInt(t.trailer);
  if (!(trailer >= tLo && trailer <= tHi)) out.trailer = `Trailer must be ${tLo} to ${tHi} ft.`;
  const [wLo, wHi] = TRUCK_LIMITS.weight_lb;
  const weight = toInt(t.weight);
  if (!(weight >= wLo && weight <= wHi)) out.weight = `Gross weight must be ${wLo.toLocaleString("en-US")} to ${lb(wHi)}.`;
  return out;
}

/** The sizes as the profile stores them, or null while any field is out of range. */
export function truckFromInputs(t: TruckInputs): { height_in: number; trailer_ft: number; weight_lb: number } | null {
  if (Object.keys(truckErrors(t)).length) return null;
  return { height_in: toInt(t.feet) * 12 + (t.inches.trim() ? toInt(t.inches) : 0), trailer_ft: toInt(t.trailer), weight_lb: toInt(t.weight) };
}

/** Empty = the server's default (null); 3 to 15 mpg = that; anything else = undefined (invalid, Save disabled). */
export function parseMpg(raw: string): number | null | undefined {
  const v = raw.trim().replace(",", ".");
  if (!v) return null;
  const n = Number(v);
  return Number.isFinite(n) && n >= 3 && n <= 15 ? Math.round(n * 10) / 10 : undefined;
}
