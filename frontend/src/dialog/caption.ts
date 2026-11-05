import type { MapsSource } from "../types";

/** `kind` "reply": the copilot's answer; "line": a spoken line (turn, nudge, parking tip). `note`: a spoken line
 *  that came while an answer was up; it shows under the answer instead of replacing it. `at`: when the caption
 *  (or its note) last changed. `sources`: Google Maps links the reply came from, shown right under it. */
export type Caption = { reply: string; heard?: string; kind?: "reply" | "line"; at?: number; note?: string; sources?: MapsSource[] };

/** How long a spoken line (alone, or as a note under an answer) stays after it was said. */
export const LINE_HOLD_MS = 8000;

/** The copilot's answer: it stays until the driver dismisses it or the next answer replaces it. Never tied to
 *  the audio: a driver who looks a minute later must still read it (UI review 2026-11-05, "Parking looks good
 *  there." replaced a fresh answer as soon as the answer's audio ended). */
export const replyCaption = (reply: string, heard?: string, now = Date.now(), sources?: MapsSource[]): Caption => ({
  reply,
  heard,
  kind: "reply",
  at: now,
  ...(sources?.length ? { sources } : {}),
});

/** A spoken line. Over an answer it becomes the answer's note (the answer text never changes); otherwise it is
 *  the caption itself and replaces an earlier line at once. */
export const lineCaption = (text: string, prev: Caption | null, now = Date.now()): Caption =>
  prev?.kind === "reply" ? { ...prev, note: text, at: now } : { reply: text, kind: "line", at: now };

/** What is left once a spoken line has had its LINE_HOLD_MS: a line disappears, an answer only loses its note. */
export const expireLine = (prev: Caption | null): Caption | null => {
  if (!prev) return prev;
  if (prev.kind === "reply") {
    if (prev.note === undefined) return prev;
    const rest = { ...prev };
    delete rest.note;
    return rest;
  }
  return null;
};

/** Whether `c` has something that expires (a line, or a note under an answer). */
export const hasExpiringLine = (c: Caption | null): boolean => !!c && (c.kind !== "reply" || c.note !== undefined);

/** Below this much visible map (layout units) the bubble would cover the whole route; the route card shows the
 *  answer at its top instead (CopilotSheet replyInSheet). The English route card leaves about 270 at the default
 *  font; the Russian one, a line or two taller, about 180. The bar used to be 180 with nothing in the route card
 *  to fall back on, so the Russian answer was on screen nowhere (UI review 2026-11-05, N1). */
export const MIN_MAP_FOR_BUBBLE = 140;

/** Whether the answer goes in the bubble over the map (true) or into the sheet (false). */
export const replyOnMap = (mapWindow: number): boolean => mapWindow >= MIN_MAP_FOR_BUBBLE;
