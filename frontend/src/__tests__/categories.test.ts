import glyphs from "@react-native-vector-icons/material-design-icons/glyphmaps/MaterialDesignIcons.json";

import { CATEGORY_ICON, categoryColor, categoryIcon } from "../constants";
import { themes } from "../theme";
import type { Category } from "../types";

// Keep in sync with src/types.ts; `satisfies` makes tsc complain when a category is missing here.
const ALL = Object.keys({
  truck_stop: true,
  hotel: true,
  rest_area: true,
  destination: true,
  service: true,
  walmart: true,
  cat_scale: true,
  truck_wash: true,
  weigh_station: true,
} satisfies Record<Category, true>) as Category[];

describe("categories", () => {
  test.each(ALL)("%s has a real icon glyph and a colour", (cat) => {
    const icon = CATEGORY_ICON[cat];
    expect(typeof icon).toBe("string");
    expect(icon in glyphs).toBe(true);
    expect(categoryIcon(cat)).toBe(icon);
    expect(categoryColor(themes.light, cat)).toMatch(/^#[0-9A-Fa-f]{6}$/);
  });

  test("an unknown runtime category falls back without throwing", () => {
    const rogue = "ev_charger" as Category;
    expect(() => categoryIcon(rogue)).not.toThrow();
    expect(categoryIcon(rogue) in glyphs).toBe(true);
    expect(categoryColor(themes.light, rogue)).toBe(themes.light.brandPrimary);
  });
});
