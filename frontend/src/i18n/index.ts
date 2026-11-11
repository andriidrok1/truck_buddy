// The app's language: English, Russian or Ukrainian. The English text is the key (t("Tap to talk")), so a string
// without a translation simply stays English; src/i18n/ru.ts holds the Russian side. The server gets the same
// language in the X-Lang header (src/auth.ts) and answers in it (backend services/i18n.py).

import { useEffect } from "react";
import { create } from "zustand";

import { storage } from "../utils/storage";
import { RU } from "./ru";

export type Lang = "en" | "ru" | "uk";
export const LANGS: { code: Lang; label: string }[] = [
  { code: "en", label: "English" },
  { code: "ru", label: "Русский" },
  { code: "uk", label: "Українська" },
];
export const LANG_KEY = "lang_v1";
/** Ukrainian is offered once src/i18n/uk.ts and the server's i18n/uk.json exist (planned right after Russian). */
export const UK_READY = false;

/** The phone's language, if it is one we speak (Hermes ships Intl, so no native module is needed). */
export function deviceLang(): Lang {
  let loc = "";
  try {
    loc = Intl.DateTimeFormat().resolvedOptions().locale || "";
  } catch {
    loc = "";
  }
  const code = loc.toLowerCase().slice(0, 2);
  // A Ukrainian phone gets English until Ukrainian is ready, never Russian.
  return code === "ru" ? "ru" : code === "uk" && UK_READY ? "uk" : "en";
}

/** `chosen`: the driver picked a language (the first screen asks until then, src/components/LanguageGate.tsx). */
type LangState = { lang: Lang; loaded: boolean; chosen: boolean; setLang: (l: Lang) => void };

export const useLangStore = create<LangState>()((set) => ({
  lang: deviceLang(),
  loaded: false,
  chosen: false,
  setLang: (lang) => {
    set({ lang, chosen: true });
    storage.setItem(LANG_KEY, lang).catch(() => {});
  },
}));

let reading: Promise<void> | null = null;
/** The saved choice, read once at startup (the device language stands until then). */
export function loadLang(): Promise<void> {
  if (!reading) {
    reading = storage
      .getItem<string | null>(LANG_KEY, null)
      .then((v) => {
        if (v === "en" || v === "ru" || v === "uk") useLangStore.setState({ lang: v, loaded: true, chosen: true });
        else useLangStore.setState({ loaded: true });
      })
      .catch(() => useLangStore.setState({ loaded: true }));
  }
  return reading;
}

/** "Delete my data": no saved choice, the phone's language until the first screen asks again (`chosen` false
 *  puts the language screen back in front of everything, app/_layout.tsx). */
export async function forgetLang(): Promise<void> {
  await storage.removeItem(LANG_KEY).catch(() => false);
  reading = Promise.resolve();
  useLangStore.setState({ lang: deviceLang(), loaded: true, chosen: false });
}

export const getLang = (): Lang => useLangStore.getState().lang;
export const setLang = (l: Lang) => useLangStore.getState().setLang(l);

/** Russian plural form: 1 миля, 2 мили, 5 миль. */
export function pluralRu(n: number, one: string, few: string, many: string): string {
  if (!Number.isInteger(n)) return few;
  const a = Math.abs(n) % 100;
  const b = a % 10;
  if (b === 1 && a !== 11) return one;
  if (b >= 2 && b <= 4 && (a < 12 || a > 14)) return few;
  return many;
}

/** A number as Russian writes it: decimal comma ("4,5"), thousands split by a no-break space ("80 000"). A four-digit
 *  number stays whole ("2026", "5000") unless the English text grouped it ("5,000" is a quantity, not a year).
 *  Anything that is not just a number (a name, "Exit 522", "12 MB") comes back as it is. */
const PLAIN_NUMBER = /^([$~]?)(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?$/;
export function numRu(v: string | number): string {
  const s = String(v);
  const m = PLAIN_NUMBER.exec(s);
  if (!m) return s;
  const [, sign, int, frac] = m;
  const digits = int.replace(/,/g, "");
  const whole = int.includes(",") || digits.length > 4 ? digits.replace(/\B(?=(\d{3})+(?!\d))/g, "\u00a0") : digits;
  return `${sign}${whole}${frac !== undefined ? `,${frac}` : ""}`;
}

/** A whole number the driver types (the weight), shown grouped in Russian ("80 000"); what they typed is kept as
 *  digits by the reader (toInt strips the spaces). English shows it as typed. QA 2026-11-09, bug 24. */
export function groupedInput(value: string, lang: Lang): string {
  if (lang !== "ru") return value;
  const digits = value.replace(/\D/g, "");
  return digits ? numRu(digits) : value;
}

/** A number in the app's language: "4.5" / "80,000" in English, "4,5" / "80 000" in Russian. */
export function formatNumber(n: number, lang: Lang = getLang(), digits?: number): string {
  const en = digits === undefined ? n.toLocaleString("en-US") : n.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  return lang === "ru" ? numRu(en) : en;
}

/** `{n}` placeholders filled from vars; a Russian `{n|plural:миля,мили,миль}` gets the number and its form. In Russian
 *  (`ru`) every value that is a plain number is written the Russian way (numRu): one place for all the callers. */
function fill(s: string, vars?: Record<string, string | number>, ru = false): string {
  return s.replace(/\{(\w+)(?:\|plural:([^}]+))?\}/g, (_m, name: string, forms?: string) => {
    const v = vars?.[name];
    if (v === undefined) return `{${name}}`;
    const shown = ru ? numRu(v) : String(v);
    if (forms) {
      const [one, few, many] = forms.split(",");
      // "80,000" is 80000; any written fraction ("5.0 mi") takes the fraction form, as "2.7 мили" does.
      const raw = String(v).replace(/,/g, "");
      return `${shown} ${raw.includes(".") ? few : pluralRu(Number(raw), one, few, many)}`;
    }
    return shown;
  });
}

type Pattern = { rx: RegExp; names: string[]; target: string; dot: boolean };
const escapeRx = (x: string) => x.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
let patterns: Pattern[] | null = null;

/** Placeholder names that always hold a number in ru.ts ("{n} min", "{h} h {m} min · {d} mi", "{f} ft {i} in"). */
const NUMERIC = new Set(["n", "d", "h", "m", "f", "i"]);
/** 12, 2.7, 80,000, $12.50, ~5 */
const NUMBER_RX = "([$~]?\\d[\\d.,]*)";

/** Dictionary keys with {placeholders} ("In {n} feet, {rest}") as regexes, the most literal text first. */
function compiled(): Pattern[] {
  if (patterns) return patterns;
  patterns = Object.entries(RU)
    .filter(([en]) => /\{\w+\}/.test(en))
    .map(([en, target]) => {
      const names: string[] = [];
      const src = en
        .split(/(\{\w+\})/)
        .map((part) => {
          const m = /^\{(\w+)\}$/.exec(part);
          if (!m) return escapeRx(part);
          names.push(m[1]);
          // A number slot takes a number only, so "{n} ft" can't swallow "Low clearance 13" and "{h} h" leaves
          // "Open 24 h" alone; any other slot may be empty: "All set{name}!" without a name.
          return NUMERIC.has(m[1]) ? NUMBER_RX : "(.*?)";
        })
        .join("");
      return { rx: new RegExp(`^${src}$`, "s"), names, target, dot: en.includes(" · "), lit: en.replace(/\{\w+\}/g, "").length };
    })
    .sort((a, b) => b.lit - a.lit)
    .map(({ rx, names, target, dot }) => ({ rx, names, target, dot }));
  return patterns;
}

/**
 * The text in the current language: an exact key, else a {placeholder} pattern whose captured values move over
 * unchanged (numbers, names), else the text as it is (a place name, or a line the server already translated).
 * Ukrainian never falls back to Russian: until uk.ts exists it stays English.
 */
export function t(en: string, vars?: Record<string, string | number>, lang: Lang = getLang()): string {
  if (lang !== "ru" || !en) return fill(en, vars);
  const exact = RU[en];
  if (exact !== undefined) return fill(exact, vars, true);
  if (vars) return fill(en, vars);
  const facts = en.includes(" · ");
  for (const p of compiled()) {
    // "{n} lb" must not swallow "14 ft 0 in · 53 ft trailer · 80,000" as its number: a line of facts only matches
    // a pattern that has the dots itself; otherwise it is translated fact by fact below.
    if (facts && !p.dot) continue;
    const m = p.rx.exec(en);
    if (!m) continue;
    const got: Record<string, string> = {};
    p.names.forEach((n, i) => {
      got[n] = m[i + 1];
    });
    if (swallowsSentence(en, Object.values(got), lang)) continue;
    return fill(p.target, Object.fromEntries(Object.entries(got).map(([k, v]) => [k, RU[v] ?? (MEASURE.test(v) ? t(v, undefined, lang) : v)])), true);
  }
  // A line of facts joined by " · " ("8 min · 2.7 mi · current", "14 ft 0 in · 53 ft trailer · 80,000 lb") that has
  // no entry as a whole: each fact on its own, so a unit never stays English next to a translated one (QA M-4).
  if (facts) return en.split(" · ").map((part) => t(part, undefined, lang)).join(" · ");
  // Two sentences with no entry as a whole ("Height must be … Gross weight must be …"): each on its own.
  const parts = sentences(en);
  if (parts.length > 1) {
    const out = parts.map((part) => t(part, undefined, lang));
    if (out.some((x, i) => x !== parts[i])) return out.join(" ");
  }
  return en;
}

/** "A. B." -> ["A.", "B."]: a break is a stop followed by a capital letter ("St. Louis" splits too, which only
 *  matters when the pieces have entries of their own). */
function sentences(en: string): string[] {
  return en.replace(/([.!?])\s+(?=[A-Z])/g, "$1\u0000").split("\u0000");
}

/** A word slot that took a whole next sentence the dictionary knows ("{max}" = "16 ft 11 in. Gross weight must be
 *  5,000 to 200,000 lb"): the match is wrong, the two sentences are translated one by one instead (QA bug 2). */
function swallowsSentence(en: string, values: string[], lang: Lang): boolean {
  return values.some((v) => {
    const pieces = sentences(v);
    if (pieces.length < 2) return false;
    const from = en.indexOf(v) + v.indexOf(pieces[1]);
    const rest = en.slice(from);
    return t(rest, undefined, lang) !== rest;
  });
}

/** A captured value that starts with a number ("10 ft", "0.4 mi", "$12.50") carries a unit worth translating;
 *  anything else (a place, a road, a name) moves over as it is. */
const MEASURE = /^[$~]?\d/;

/** t() for components: re-renders when the language changes. */
export function useT() {
  const lang = useLangStore((s) => s.lang);
  useEffect(() => {
    loadLang();
  }, []);
  return (en: string, vars?: Record<string, string | number>) => t(en, vars, lang);
}

export const useLang = () => useLangStore((s) => s.lang);

// The saved choice is read as soon as anything imports this module (the root layout does, through src/auth.ts),
// not by a hook: no screen called the hook, and after a restart the phone's language came back (review 2026-11-05).
void loadLang();
