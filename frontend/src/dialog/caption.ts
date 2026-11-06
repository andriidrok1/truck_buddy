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

/** Bubble height for one line of the answer (captionText lineHeight 24 at font scale 1). */
export const REPLY_LINE = 24;

/** Below this much room the bubble cannot hold "You said", one line and the paddings; the answer goes to the sheet. */
export const MIN_BUBBLE_ROOM = MIN_MAP_FOR_BUBBLE - 32;

/** The height the bubble may take: what is left of the map window above the bubble's bottom edge (a card under
 *  the bubble lifts it), but at most 3/4 of the window so some of the route stays in sight above it. `lift`: how
 *  far the bubble's bottom sits above the sheet's top edge; 14 = the tail plus a gap to the top bar. */
export const bubbleRoom = (mapWindow: number, lift: number): number => Math.min(mapWindow - lift - 14, mapWindow * 0.75);

/** Measured parts of the bubble. `textY`: where the answer text starts inside the scroll content (padding and
 *  "You said"); `contentH`: the whole scroll content; `belowH`: the note and padding under the scroll area. */
export type BubbleMeasure = { room: number; textY: number; contentH: number; belowH: number; line?: number };

/** Whether the whole answer fits in the bubble, and when it does not, how tall the scroll area may be: the
 *  answer's lines are cut at a line boundary, never through the letters (UI review 2026-11-05, third pass, H1:
 *  a 118 pt bubble showed three and a half lines of a four-line answer, the last one cut mid-letter). The
 *  caller moves the answer into the sheet when it does not fit and the sheet has a place for it. */
export const fitBubble = ({ room, textY, contentH, belowH, line = REPLY_LINE }: BubbleMeasure): { fits: boolean; scrollH?: number } => {
  if (contentH + belowH <= room + 0.5) return { fits: true };
  const lines = Math.max(1, Math.floor((room - belowH - textY) / line));
  return { fits: false, scrollH: textY + lines * line };
};

/** Where the answer goes: the bubble over the map, or the sheet (the route card's top in the preview, under
 *  "Ask your copilot" when idle). The sheet takes it when the map window is too small, when the room above a card
 *  is too small, or when the answer was measured not to fit whole (`overflowed`) and the sheet has a place for it
 *  (`sheetHosts`); elsewhere the bubble keeps it and scrolls at a line boundary. */
export const replyPlacement = (p: { mapWindow: number; room: number; overflowed: boolean; sheetHosts: boolean }): "map" | "sheet" =>
  !replyOnMap(p.mapWindow) || p.room < MIN_BUBBLE_ROOM || (p.overflowed && p.sheetHosts) ? "sheet" : "map";

/** The copilot's answer (not a spoken turn or nudge line): what the hardware Back may close. While driving the
 *  caption is usually the turn instruction, and Back took it away (#124 review). */
export const isReplyCaption = (c: Caption | null): boolean => !!c && c.kind !== "line";
