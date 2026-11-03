/** "Reno, NV, United States" -> "Reno, NV": every place is in the US, and the country cost the banner the
 *  end of the name (QA 2026-11-02, bug 34). */
export function withoutCountry(name: string): string {
  return name.replace(/,\s*(United States( of America)?|USA|US)$/i, "");
}
