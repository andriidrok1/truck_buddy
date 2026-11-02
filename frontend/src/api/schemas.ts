// Runtime shape checks for the two answers the app cannot work without. Loose on purpose: only the fields
// the app reads are required, everything else passes through, so a server that adds a field never breaks
// the app. A failed check is reported (console in dev, Sentry in builds through the sink) and the raw
// object is used anyway; the alternative, throwing, would turn a harmless addition into a broken turn.
import { z } from "zod";

export const PlaceSchema = z.looseObject({
  id: z.string(),
  name: z.string(),
  category: z.string(),
  lat: z.number(),
  lng: z.number(),
});

export const RouteInfoSchema = z.looseObject({
  geometry: z.array(z.array(z.number()).min(2)),
  distance_m: z.number(),
  duration_s: z.number(),
  distance_mi: z.number(),
  duration_min: z.number(),
  summary: z.string(),
  source: z.string(),
  maneuvers: z.array(z.looseObject({ action: z.string(), instruction: z.string(), lat: z.number(), lng: z.number(), index: z.number() })).optional(),
});

export const RouteResultSchema = z.looseObject({
  place: PlaceSchema,
  route: RouteInfoSchema,
});

export const AgentResponseSchema = z.looseObject({
  status: z.string(),
  transcript: z.string(),
  reply: z.string(),
  source: z.string(),
  place: PlaceSchema.nullish(),
  route: RouteInfoSchema.nullish(),
  options: z.array(PlaceSchema).optional(),
  actions: z.array(z.looseObject({ type: z.string() })).optional(),
});

export type ContractWarningSink = (path: string, issues: string[]) => void;
let sink: ContractWarningSink = (path, issues) => console.warn(`[contract] ${path}: ${issues.join("; ")}`);

/** Where contract failures go besides the console (Sentry in builds). */
export function setContractWarningSink(fn: ContractWarningSink) {
  sink = fn;
}

/** The parsed value when it fits, the raw value (reported once) when it does not; never throws. */
export function checkContract<T>(schema: z.ZodType<unknown>, value: T, path: string): T {
  const r = schema.safeParse(value);
  if (r.success) return value;
  const issues = r.error.issues.slice(0, 5).map((i) => `${i.path.join(".") || "<root>"}: ${i.message}`);
  try {
    sink(path, issues);
  } catch {
    // reporting must never fail the request
  }
  return value;
}
