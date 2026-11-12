// Contrast of the language screen and the intro (WCAG 2.x), computed from the theme tokens in both schemes, so a
// token change or a new colour on these screens cannot bring back what the review of #169 found: green verdict
// text at 2:1, white on green tags, pale page dots.
import fs from "fs";
import path from "path";

import { themes, type ThemeColors } from "../theme";

function luminance(hex: string): number {
  const m = /^#([0-9a-f]{6})$/i.exec(hex);
  if (!m) throw new Error(`not an opaque hex colour: ${hex}`);
  const [r, g, b] = [0, 2, 4].map((i) => {
    const c = parseInt(m[1].slice(i, i + 2), 16) / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}
export function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

type Token = keyof ThemeColors;
const TEXT = 4.5; // AA for text
const SHAPE = 3; // AA for what is not text: dots, rings, icons
/** [what, foreground, background, minimum] as the components use them. */
const PAIRS: [string, Token, Token, number][] = [
  // LanguageGate
  ["language title", "onSurface", "surface", TEXT],
  ["Russian line of the title", "onSurfaceSecondary", "surface", TEXT],
  ["language row, not picked", "onSurface", "surfaceSecondary", TEXT],
  ["empty ring on a row not picked", "onSurfaceSecondary", "surfaceSecondary", SHAPE],
  ["check mark", "onBrandPrimary", "brandPrimary", SHAPE],
  ["primary button", "onBrandPrimary", "brandPrimary", TEXT],
  // IntroGate
  ["page dot, current", "brandPrimary", "surface", SHAPE],
  ["page dot, other", "muted", "surface", SHAPE],
  // the screens
  ["heading", "onSurface", "surface", TEXT],
  ["text under a heading, YOU SAY / I ANSWER", "onSurfaceSecondary", "surface", TEXT],
  ["answer card: place and verdict", "onSurface", "surfaceSecondary", TEXT],
  ["answer card: distance", "onSurfaceSecondary", "surfaceSecondary", TEXT],
  ["pin icon", "onInfo", "info", SHAPE],
  ["way-in arrow", "onInfo", "info", SHAPE],
  ["tags on the picture", "onSurface", "surface", TEXT],
  ["card on the picture: place and verdict", "onSurface", "surface", TEXT],
  ["card under the picture (large font)", "onSurface", "surfaceSecondary", TEXT],
];

describe.each(["light", "dark"] as const)("%s theme", (scheme) => {
  const c = themes[scheme];
  test.each(PAIRS)("%s: %s on %s is at least %s:1", (_what, fg, bg, min) => {
    expect(contrast(c[fg], c[bg])).toBeGreaterThanOrEqual(min);
  });
});

test("the checker itself", () => {
  expect(contrast("#000000", "#FFFFFF")).toBeCloseTo(21, 5);
  expect(contrast("#34C759", "#FFFFFF")).toBeLessThan(SHAPE); // why the verdict is not written in green
  expect(contrast(themes.light.muted, themes.light.surfaceSecondary)).toBeLessThan(SHAPE); // why the ring is not `muted`
});

test("the screens use those pairs: no text in the success or info colour except Skip, no borderStrong", () => {
  const dir = path.join(__dirname, "..", "components");
  const files = ["LanguageGate.tsx", ...fs.readdirSync(path.join(dir, "intro")).map((f) => path.join("intro", f))];
  const src = Object.fromEntries(files.map((f) => [f, fs.readFileSync(path.join(dir, f), "utf8")]));
  for (const [f, s] of Object.entries(src)) {
    expect([f, /\bcolor: colors\.(success|warning|muted|onSurfaceTertiary)\b/.test(s)]).toEqual([f, false]);
    expect([f, /colors\.borderStrong\b/.test(s)]).toEqual([f, false]);
    expect([f, /color: colors\.onSuccess\b/.test(s)]).toEqual([f, false]);
  }
  // "Skip" is a link in `info` (4.02:1 light), the same as the links on the consent screen: the one exception.
  const infoText = Object.entries(src).flatMap(([f, s]) => (s.match(/\bcolor: colors\.info\b/g) ?? []).map(() => f));
  expect(infoText).toEqual([path.join("intro", "IntroGate.tsx")]);
});
