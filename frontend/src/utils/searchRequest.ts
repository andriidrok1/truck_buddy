import { getLang, type Lang } from "../i18n";

const REQUEST_EN = /\b(?:i|i'm|me|my|need|find|take|go|where|show|get|park|near|any|truck ?stops?|hotels?|motels?|rest ?areas?|fuel|diesel|gas|parking)\b/i;
// The same idea in Russian. \b knows Latin letters only, so a word's edge is "start of the text or anything that is
// not a Cyrillic letter" before it, and the same after it for the whole words.
// Whole words: short ones that also open place names ("я" must not catch Якутск, "как" Каково, "душ" Душанбе,
// "есть" Естественная улица, "весы" Весёлое).
const WORDS_RU = "я|мне|меня|мой|моя|моё|мои|что|когда|как|какой|какая|какие|какое|сколько|есть|где|надо|нужно|хочу|душ|еда|азс|весы|спать|поесть|домой";
// Word starts: "заправ" covers заправка, заправку, заправиться; "парковк" leaves Парковая улица a place.
const STARTS_RU =
  "найди|найти|нужен|нужн|покажи|отвези|маршрут|поехали|едем|отмени|останови|устал|переночев|ночев|ночлег|ночь|рядом|дизел|топлив|заправ|автозаправ|стоянк|автостоянк|парковк|припарк|трак[- ]?стоп|мотел|отел|мойк|погод|поломк|пробк|туалет|кофе";
const REQUEST_RU = new RegExp(`(?:^|[^а-яё])(?:(?:${WORDS_RU})(?![а-яё])|${STARTS_RU})`, "i");

/** What "Where to?" sends: a bare place ("Reno") becomes "Take me to Reno"; a full request ("I need fuel") goes as
 *  typed. In Russian the bare place becomes "Маршрут до Рино" and a Russian request goes as typed: "найди дизель"
 *  used to reach the copilot as "Take me to найди дизель" (QA 2026-11-11). Ukrainian stays on the English path
 *  until it has its own texts. */
export function searchRequest(q: string, lang: Lang = getLang()): string {
  if (lang !== "ru") return REQUEST_EN.test(q) ? q : `Take me to ${q}`;
  return REQUEST_EN.test(q) || REQUEST_RU.test(q) ? q : `Маршрут до ${q}`;
}
