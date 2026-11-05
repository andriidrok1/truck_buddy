import type { MapsSource } from "../types";

/** `ended`: the answer's own audio has finished and `at` is that moment. `sources`: Google Maps links the reply
 *  came from, shown right under it (a grounded answer must carry them). */
export type Caption = { reply: string; heard?: string; kind?: "reply" | "line"; at?: number; ended?: boolean; sources?: MapsSource[] };

/** How long the copilot's answer stays over a spoken line, counted from when its audio ended. Counted from
 *  its arrival (6 s), a 5 s answer to "I just swapped to a 48 foot trailer" was gone a second after it was
 *  said and the caption read "In 500 feet, turn left…" (bug 12). The turn itself is on the nav banner. */
export const REPLY_HOLD_MS = 8000;

/** The copilot's answer: kept until the next turn (a driver who looks later must still see it). */
export const replyCaption = (reply: string, heard?: string, now = Date.now(), sources?: MapsSource[]): Caption => ({
  reply,
  heard,
  kind: "reply",
  at: now,
  ...(sources?.length ? { sources } : {}),
});

/** A spoken line (turn, nudge, parking tip): gone 8 s after it is said, and never over a fresh answer. */
export const lineCaption = (text: string, prev: Caption | null, now = Date.now()): Caption =>
  prev?.kind === "reply" && now - (prev.at ?? 0) < REPLY_HOLD_MS ? prev : { reply: text, kind: "line", at: now };

/** Audio just ended. The first end after an answer is the answer's own: its hold starts now. Later ends are
 *  lines the queue played under it and must not stretch the hold. */
export const replyEnded = (prev: Caption | null, now = Date.now()): Caption | null =>
  prev?.kind === "reply" && !prev.ended ? { ...prev, at: now, ended: true } : prev;
