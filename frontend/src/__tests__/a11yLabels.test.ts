// Every accessibilityLabel / accessibilityHint goes through t(): TalkBack read "Where to?", "Go", "Tap to talk" in
// English to a Russian driver (QA 2026-11-09, bug 12). A plain string, a template or a bare variable fails here;
// `t("Go")`, `cond ? t("A") : t("B")`, `t("Remove stop {name}", { name })` pass. app/tester.tsx is the developer
// screen and stays English.
import fs from "fs";
import path from "path";

const ROOT = path.join(__dirname, "..", "..");
const SKIP = new Set([path.join("app", "tester.tsx")]);

function files(dir: string): string[] {
  return fs.readdirSync(path.join(ROOT, dir), { withFileTypes: true }).flatMap((e) => {
    const rel = path.join(dir, e.name);
    if (e.isDirectory()) return e.name === "__tests__" || e.name === "node_modules" ? [] : files(rel);
    return /\.tsx?$/.test(e.name) && !SKIP.has(rel) ? [rel] : [];
  });
}

/** The `{...}` expression that starts at `i` (an opening brace), braces inside strings ignored. */
function braced(src: string, i: number): string {
  let depth = 0;
  let quote: string | null = null;
  for (let j = i; j < src.length; j++) {
    const c = src[j];
    if (quote) {
      if (c === "\\") j++;
      else if (c === quote) quote = null;
      continue;
    }
    if (c === '"' || c === "'" || c === "`") quote = c;
    else if (c === "{") depth++;
    else if (c === "}" && --depth === 0) return src.slice(i + 1, j);
  }
  return src.slice(i + 1);
}

/** The expression with every t(...) / tr(...) call taken out, so what is left is what reaches the reader as is. */
function withoutT(expr: string): string {
  let out = "";
  for (let i = 0; i < expr.length; i++) {
    const m = /^\b(?:t|tr)\(/.exec(expr.slice(i));
    if (m && (i === 0 || !/[\w.$]/.test(expr[i - 1]))) {
      let depth = 0;
      let j = i + m[0].length - 1;
      for (; j < expr.length; j++) {
        if (expr[j] === "(") depth++;
        else if (expr[j] === ")" && --depth === 0) break;
      }
      out += "T";
      i = j;
    } else out += expr[i];
  }
  return out;
}

export function untranslatedLabels(rel: string, src: string): string[] {
  const bad: string[] = [];
  const rx = /accessibility(?:Label|Hint)=/g;
  let m: RegExpExecArray | null;
  while ((m = rx.exec(src))) {
    const at = m.index + m[0].length;
    const line = src.slice(0, m.index).split("\n").length;
    const where = `${rel}:${line}`;
    if (src[at] !== "{") {
      bad.push(`${where} ${src.slice(m.index, src.indexOf("\n", m.index)).trim()}`);
      continue;
    }
    const expr = braced(src, at);
    // A literal compared in a condition (state === "peek") is never read out.
    const rest = withoutT(expr).replace(/(?:===|!==)\s*(["'])[^"']*\1/g, "=== X").replace(/(["'])[^"']*\1\s*(?:===|!==)/g, "X ===");
    if (!/\bT\b/.test(rest) || /["'`]/.test(rest)) bad.push(`${where} ${m[0]}{${expr}}`);
  }
  return bad;
}

test("the checker itself: literals and bare values fail, t() passes", () => {
  const check = (s: string) => untranslatedLabels("x.tsx", s).length;
  expect(check('<A accessibilityLabel="Go" />')).toBe(1);
  expect(check("<A accessibilityLabel={`Route to ${name}`} />")).toBe(1);
  expect(check('<A accessibilityLabel={on ? "Mute" : t("Unmute")} />')).toBe(1);
  expect(check("<A accessibilityLabel={label} />")).toBe(1);
  expect(check('<A accessibilityLabel={t("Go")} />')).toBe(0);
  expect(check('<A accessibilityLabel={on ? t("Mute") : t("Unmute")} />')).toBe(0);
  expect(check('<A accessibilityLabel={tr("Route to {name}", { name: p.name })} />')).toBe(0);
  expect(check('<A accessibilityHint={h ? t("Saved") : undefined} />')).toBe(0);
  expect(check('<A accessibilityLabel={s === "peek" ? t("Show") : t("Hide")} />')).toBe(0);
});

test("no accessibility label or hint in app/ and src/ skips t()", () => {
  const bad = [...files("app"), ...files("src")].flatMap((rel) => untranslatedLabels(rel, fs.readFileSync(path.join(ROOT, rel), "utf8")));
  expect(bad).toEqual([]);
});
