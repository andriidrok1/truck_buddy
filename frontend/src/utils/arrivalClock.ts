import type { Lang } from "../i18n";

/** A clock time in the app's language, not the phone's: 24-hour in Russian ("17:28"), the phone's format in
 *  English ("5:28 PM"). A Russian app on an English phone showed "11:40 PM" in the history (QA 2026-11-09, bug 10). */
export function clockTime(d: Date, lang: Lang = "en"): string {
  if (lang === "ru") return `${d.getHours()}:${String(d.getMinutes()).padStart(2, "0")}`;
  return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

/** The arrival time: the phone's own format in English ("5:28 PM"), 24-hour in Russian ("17:28"). */
export function arrivalClock(seconds: number, lang: Lang = "en", now = Date.now()) {
  return clockTime(new Date(now + seconds * 1000), lang);
}

const MONTHS_RU = ["янв.", "февр.", "марта", "апр.", "мая", "июня", "июля", "авг.", "сент.", "окт.", "нояб.", "дек."];

/** "Oct 28" / "28 окт.": a day in the app's language. */
export function shortDate(d: Date, lang: Lang = "en"): string {
  if (lang === "ru") return `${d.getDate()} ${MONTHS_RU[d.getMonth()]}`;
  return d.toLocaleDateString([], { month: "short", day: "numeric" });
}

/** "10/28/2026" / "28.10.2026". */
export function numericDate(d: Date, lang: Lang = "en"): string {
  if (lang === "ru") return `${String(d.getDate()).padStart(2, "0")}.${String(d.getMonth() + 1).padStart(2, "0")}.${d.getFullYear()}`;
  return d.toLocaleDateString();
}
