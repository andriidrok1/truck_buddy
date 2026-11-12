import { getLang, type Lang } from "../i18n";

const REQUEST_EN = /\b(?:i|i'm|me|my|need|find|take|go|where|show|get|park|near|any|truck ?stops?|hotels?|motels?|rest ?areas?|fuel|diesel|gas|parking)\b/i;
// The same idea in Russian. \b knows Latin letters only, so the edge is "start of the text or anything that is not a
// Cyrillic letter". Most entries are word starts ("заправк" covers заправка, заправку, заправки); the short whole
// words need their end too, or "как" would catch a town that merely starts with it.
const REQUEST_RU =
  /(?:^|[^а-яё])(?:(?:где|как|надо|хочу|весы)(?![а-яё])|найди|найти|нужен|нужн|покажи|отвези|маршрут|поехали|рядом|сколько|дизел|топлив|заправ|стоянк|парковк|припарк|трак-?стоп|мотел|отел|мойк|погод|поломк|ночь|ночев)/i;

/** What "Where to?" sends: a bare place ("Reno") becomes "Take me to Reno"; a full request ("I need fuel") goes as
 *  typed. In Russian the bare place becomes "Маршрут до Рино" and a Russian request goes as typed: "найди дизель"
 *  used to reach the copilot as "Take me to найди дизель" (QA 2026-11-11). Ukrainian stays on the English path
 *  until it has its own texts. */
export function searchRequest(q: string, lang: Lang = getLang()): string {
  if (lang !== "ru") return REQUEST_EN.test(q) ? q : `Take me to ${q}`;
  return REQUEST_EN.test(q) || REQUEST_RU.test(q) ? q : `Маршрут до ${q}`;
}
