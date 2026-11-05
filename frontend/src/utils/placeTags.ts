// The tag line under a place name ("DEF · 24/7 · 7 truck spots · (916) 372-2893"). Tags come from the backend in
// English (backend/scripts/build_places.py); each one goes through the dictionary on its own, because the joined
// line never matches a key. A phone number among them is written the US way instead of ten bare digits.

/** "9163722893" / "+1 916-372-2893" -> "(916) 372-2893"; anything that is not a US phone number -> null. */
export function formatUsPhone(tag: string): string | null {
  if (!/^[\d\s()+.-]+$/.test(tag)) return null;
  let d = tag.replace(/\D/g, "");
  if (d.length === 11 && d.startsWith("1")) d = d.slice(1);
  if (d.length !== 10) return null;
  return `(${d.slice(0, 3)}) ${d.slice(3, 6)}-${d.slice(6)}`;
}

/** The tags joined with " · ", each translated by `tr` (t from src/i18n), phone numbers formatted. */
export function placeTagLine(tags: string[], tr: (en: string) => string): string {
  return tags.map((tag) => formatUsPhone(tag) ?? tr(tag)).join(" · ");
}
