// The `hint` field of /voice/transcribe: names the recogniser should expect (the destination,
// the places on screen, the stops, the roads of the route), comma-separated. Pure.

/** The backend takes up to 600; a shorter hint keeps the multipart form small. */
export const STT_HINT_MAX_CHARS = 400;

/**
 * Trims, drops empties, strips commas inside names (the comma is the separator), dedupes
 * case-insensitively and keeps the order given, so the most useful names go first. Stops
 * adding before the joined string would pass `maxChars`.
 */
export function buildSttHint(parts: readonly (string | null | undefined)[], maxChars = STT_HINT_MAX_CHARS): string {
  const seen = new Set<string>();
  const kept: string[] = [];
  let length = 0;
  for (const raw of parts) {
    const name = (raw ?? "").replace(/,/g, " ").replace(/\s+/g, " ").trim();
    if (!name) continue;
    const key = name.toLowerCase();
    if (seen.has(key)) continue;
    const next = length + name.length + (kept.length ? 2 : 0);
    if (next > maxChars) continue; // a shorter name later may still fit
    seen.add(key);
    kept.push(name);
    length = next;
  }
  return kept.join(", ");
}

/** The road a maneuver leads onto ("Turn right onto I-80 E." -> "I-80 E"), or null. */
export function streetFromInstruction(instruction: string | null | undefined): string | null {
  const m = instruction?.match(/\b(?:onto|toward|towards)\s+(.+?)\.?$/i);
  return m ? m[1].trim() || null : null;
}
