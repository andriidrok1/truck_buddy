// What the turn banner prints when the instruction is too long for it. HERE names every sign on the gantry:
// "Take exit 52B toward Sacramento International Airport/Woodland onto I-5 N", "Съезд 518 в сторону US-50 W/San
// Francisco/I-80 на I-80-BR W (Capital City Fwy)". Four lines of a narrow column cut that in the middle, and the
// driver lost the part he steers by (QA 2026-11-09 and 2026-11-11). The voice still says the whole sentence; the
// banner keeps the exit number and the first direction.

/** Longer than this, an instruction is shortened, and if it is still longer it is printed a size smaller. */
export const LONG_INSTRUCTION = 40;

// Each sign list runs from its lead word to the next lead word: "for X/Y toward Z" is two lists, not one (review of
// #178: "...I-80 W for San Francisco/Oakland toward Bay Bridge" lost "toward Bay Bridge").
const DIRECTION_WORDS = "toward|towards|for|в сторону";
const LEADS = `${DIRECTION_WORDS}|onto|на`;
const LIST = new RegExp(`(\\s(?:${LEADS})\\s)(.+?)(?=\\s(?:${LEADS})\\s|$)`, "gi");
const DIRECTION = new RegExp(`\\s(?:${DIRECTION_WORDS})\\s`, "i");
const ONTO = new RegExp(`\\s(?:onto|на)\\s.+?(?=\\s(?:${DIRECTION_WORDS})\\s|$)`, "gi");
// An exit: a number ("exit 52B"), a range ("exit 33-34", "exit 33A/B"), or none ("Take the exit toward ...").
// No \b before "съезд": in JS \b only knows Latin letters.
const EXIT = /(?:^|\s)(?:exit|съезд)\s+\d+[A-Z]?(?:\s*[-–/]\s*\d*[A-Z]?)?(?=\s|$)|^(?:take (?:the )?)?exit\b|^съезд(?=\s|$)/i;

/** "A/B/C" -> "A": the first name on a sign list is the one the sign leads with. */
const firstOf = (list: string) => list.split("/")[0].trim();

/**
 * The banner's text for an instruction. Short ones come back as they are. A long one loses, in this order:
 * - the bracketed road name: "(Capital City Fwy)";
 * - every name but the first in a list after "toward" / "в сторону" and after "onto" / "на";
 * - on an exit with a direction (numbered, a range like "33-34", or none), the road after it: the exit and the sign
 *   are what the driver looks for;
 * - "Take exit" / "Take the exit" becomes "Exit", the trailing full stop goes.
 */
export function bannerInstruction(instruction: string): string {
  const text = instruction.trim();
  if (text.length <= LONG_INSTRUCTION) return text;
  let s = text
    .replace(/\.$/, "")
    .replace(/\s*\([^)]*\)/g, "")
    .replace(/^take (?:the )?exit\b/i, "Exit");
  s = s.replace(LIST, (_m, lead: string, list: string) => lead + firstOf(list));
  if (EXIT.test(s) && DIRECTION.test(s)) s = s.replace(ONTO, "");
  return s.replace(/\s+/g, " ").trim();
}
