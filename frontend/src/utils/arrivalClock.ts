import type { Lang } from "../i18n";

/** The arrival time: the phone's own format in English ("5:28 PM"), 24-hour in Russian ("17:28"). */
export function arrivalClock(seconds: number, lang: Lang = "en", now = Date.now()) {
  const d = new Date(now + seconds * 1000);
  if (lang === "ru") return `${d.getHours()}:${String(d.getMinutes()).padStart(2, "0")}`;
  return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}
