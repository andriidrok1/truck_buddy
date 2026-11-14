// What the turn banner prints when the instruction is too long for it. HERE names every sign on the gantry:
// "Take exit 52B toward Sacramento International Airport/Woodland onto I-5 N", "Съезд 518 в сторону US-50 W/San
// Francisco/I-80 на I-80-BR W (Capital City Fwy)". Four lines of a narrow column cut that in the middle, and the
// driver lost the part he steers by (QA 2026-11-09 and 2026-11-11). The voice still says the whole sentence; the
// banner keeps the exit number and the first direction.

/** Longer than this, an instruction is shortened, and if it is still longer it is printed a size smaller. */
export const LONG_INSTRUCTION = 40;

const TOWARD = /(\s(?:toward|towards|for|в сторону)\s)(.+?)(?=\s(?:onto|на)\s|$)/i;
const ONTO = /(\s(?:onto|на)\s)(.+?)(?=\s(?:toward|towards|for|в сторону)\s|$)/i;
// No \b before "съезд": in JS \b only knows Latin letters.
const EXIT = /(?:^|\s)(?:exit|съезд)\s+\d+[A-Z]?(?=\s|$)/i;

/** "A/B/C" -> "A": the first name on a sign list is the one the sign leads with. */
const firstOf = (list: string) => list.split("/")[0].trim();

/**
 * The banner's text for an instruction. Short ones come back as they are. A long one loses, in this order:
 * - the bracketed road name: "(Capital City Fwy)";
 * - every name but the first in a list after "toward" / "в сторону" and after "onto" / "на";
 * - on an exit with a direction, the road after it: the exit number and the sign are what the driver looks for;
 * - "Take exit" becomes "Exit", the trailing full stop goes.
 */
export function bannerInstruction(instruction: string): string {
  const text = instruction.trim();
  if (text.length <= LONG_INSTRUCTION) return text;
  let s = text
    .replace(/\.$/, "")
    .replace(/\s*\([^)]*\)/g, "")
    .replace(/^take exit\b/i, "Exit");
  s = s.replace(TOWARD, (_m, lead: string, list: string) => lead + firstOf(list));
  s = s.replace(ONTO, (_m, lead: string, list: string) => lead + firstOf(list));
  if (EXIT.test(s) && TOWARD.test(s)) s = s.replace(ONTO, "");
  return s.replace(/\s+/g, " ").trim();
}
