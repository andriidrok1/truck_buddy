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

/** `{n}` placeholders filled from vars; a Russian `{n|plural:миля,мили,миль}` gets the number and its form. */
function fill(s: string, vars?: Record<string, string | number>): string {
  return s.replace(/\{(\w+)(?:\|plural:([^}]+))?\}/g, (_m, name: string, forms?: string) => {
    const v = vars?.[name];
    if (v === undefined) return `{${name}}`;
    if (forms) {
      const [one, few, many] = forms.split(",");
      // "80,000" is 80000; any written fraction ("5.0 mi") takes the fraction form, as "2.7 мили" does.
      const raw = String(v).replace(/,/g, "");
      return `${v} ${raw.includes(".") ? few : pluralRu(Number(raw), one, few, many)}`;
    }
    return String(v);
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
  if (exact !== undefined) return fill(exact, vars);
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
    return fill(p.target, Object.fromEntries(Object.entries(got).map(([k, v]) => [k, RU[v] ?? (MEASURE.test(v) ? t(v, undefined, lang) : v)])));
  }
  // A line of facts joined by " · " ("8 min · 2.7 mi · current", "14 ft 0 in · 53 ft trailer · 80,000 lb") that has
  // no entry as a whole: each fact on its own, so a unit never stays English next to a translated one (QA M-4).
  if (facts) return en.split(" · ").map((part) => t(part, undefined, lang)).join(" · ");
  return en;
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
