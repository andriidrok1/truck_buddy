import { availableStates, dragHeight, FLING_VELOCITY, RUBBER_MAX, rubberBand, sheetHeights, snapTarget, stateHeight } from "../utils/sheetSnap";

// A route card that fits: body not clipped.
const fits = sheetHeights({ peek: 150, auto: 360, content: 360, window: 844 });
// An options list clipped in auto: full exists.
const clipped = sheetHeights({ peek: 120, auto: 420, content: 900, window: 844 });

describe("sheetHeights", () => {
  test("no full state when the content fits the auto height", () => {
    expect(fits).toEqual({ peek: 150, auto: 360 });
    expect(availableStates(fits)).toEqual(["peek", "auto"]);
    // A rounding hair of extra content is not a reason to offer full.
    expect(sheetHeights({ peek: 150, auto: 360, content: 361, window: 844 }).full).toBeUndefined();
  });

  test("full is the content height, capped at 85% of the window", () => {
    expect(clipped.full).toBeCloseTo(844 * 0.85);
    expect(sheetHeights({ peek: 120, auto: 420, content: 500, window: 844 }).full).toBe(500);
    expect(availableStates(clipped)).toEqual(["peek", "auto", "full"]);
  });

  test("peek never exceeds auto; without a shorter peek only auto remains", () => {
    const h = sheetHeights({ peek: 500, auto: 360, content: 360, window: 844 });
    expect(h.peek).toBe(360);
    expect(availableStates(h)).toEqual(["auto"]);
  });

  test("stateHeight falls back to auto for a missing full", () => {
    expect(stateHeight("full", fits)).toBe(360);
    expect(stateHeight("peek", fits)).toBe(150);
    expect(stateHeight("full", clipped)).toBe(clipped.full);
  });
});

describe("dragHeight", () => {
  test("follows the finger between the ends", () => {
    expect(dragHeight(360, 100, fits)).toBe(260);
    expect(dragHeight(420, -200, clipped)).toBe(620);
  });

  test("rubber-bands past the tallest state, never more than RUBBER_MAX", () => {
    expect(dragHeight(360, -10, fits)).toBeCloseTo(360 + rubberBand(10));
    expect(dragHeight(360, -1000, fits)).toBeLessThan(360 + RUBBER_MAX);
    expect(dragHeight(360, -1000, fits)).toBeGreaterThan(360 + RUBBER_MAX * 0.9);
    expect(dragHeight(clipped.full!, -500, clipped)).toBeLessThan(clipped.full! + RUBBER_MAX);
  });

  test("rubber-bands below peek too", () => {
    expect(dragHeight(150, 500, fits)).toBeGreaterThan(150 - RUBBER_MAX);
    expect(dragHeight(150, 500, fits)).toBeLessThan(150);
  });

  test("rubberBand is monotonic and bounded", () => {
    expect(rubberBand(0)).toBe(0);
    expect(rubberBand(RUBBER_MAX)).toBe(RUBBER_MAX / 2);
    expect(rubberBand(10000)).toBeLessThan(RUBBER_MAX);
  });
});

describe("snapTarget", () => {
  test("slow release snaps to the nearest available height", () => {
    expect(snapTarget(200, 0, fits, "auto")).toBe("peek");
    expect(snapTarget(300, 0, fits, "auto")).toBe("auto");
    expect(snapTarget(380, 0, fits, "auto")).toBe("auto"); // rubber-band overshoot, no full
    expect(snapTarget(650, 50, clipped, "auto")).toBe("full");
    expect(snapTarget(500, 50, clipped, "auto")).toBe("auto");
  });

  test("a fling moves one state in its direction from where the drag began", () => {
    expect(snapTarget(400, -FLING_VELOCITY, clipped, "auto")).toBe("full");
    expect(snapTarget(400, FLING_VELOCITY, clipped, "auto")).toBe("peek");
    expect(snapTarget(130, -2000, clipped, "peek")).toBe("auto");
    expect(snapTarget(700, 2000, clipped, "full")).toBe("auto");
  });

  test("a fling cannot leave the available states", () => {
    expect(snapTarget(360, -2000, fits, "auto")).toBe("auto");
    expect(snapTarget(150, 2000, fits, "peek")).toBe("peek");
  });

  test("below fling speed the position still decides", () => {
    expect(snapTarget(200, -(FLING_VELOCITY - 1), fits, "auto")).toBe("peek");
  });
});
