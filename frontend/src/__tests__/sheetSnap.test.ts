import { dragProgress, FLING_VELOCITY, heightAt, progressFor, sheetHeights, snapTarget } from "../utils/sheetSnap";

const h = sheetHeights(260, 640);

describe("sheetHeights", () => {
  test("tall state is never shorter than the content", () => {
    expect(sheetHeights(700, 640)).toEqual({ collapsed: 700, expanded: 700 });
    expect(sheetHeights(260, 640)).toEqual({ collapsed: 260, expanded: 640 });
  });
});

describe("heightAt / progressFor", () => {
  test("round-trip between the two states", () => {
    expect(heightAt(0, h)).toBe(260);
    expect(heightAt(1, h)).toBe(640);
    expect(heightAt(0.5, h)).toBe(450);
    expect(progressFor(450, h)).toBe(0.5);
    expect(progressFor(100, h)).toBe(0);
    expect(progressFor(900, h)).toBe(1);
  });

  test("no span means no progress", () => {
    expect(progressFor(700, sheetHeights(700, 640))).toBe(0);
  });
});

describe("dragProgress", () => {
  test("dragging up from collapsed grows the sheet; down shrinks it", () => {
    expect(dragProgress(0, -190, h)).toBeCloseTo(0.5);
    expect(dragProgress(1, 190, h)).toBeCloseTo(0.5);
  });

  test("clamped at both ends", () => {
    expect(dragProgress(0, -1000, h)).toBe(1);
    expect(dragProgress(0, 50, h)).toBe(0);
    expect(dragProgress(1, -50, h)).toBe(1);
  });
});

describe("snapTarget", () => {
  test("slow release snaps to the nearer height", () => {
    expect(snapTarget(300, 0, h)).toBe("collapsed");
    expect(snapTarget(449, 0, h)).toBe("collapsed");
    expect(snapTarget(450, 0, h)).toBe("expanded");
    expect(snapTarget(600, 100, h)).toBe("expanded");
  });

  test("a fling is obeyed regardless of position", () => {
    expect(snapTarget(280, -FLING_VELOCITY, h)).toBe("expanded");
    expect(snapTarget(280, -2000, h)).toBe("expanded");
    expect(snapTarget(620, FLING_VELOCITY, h)).toBe("collapsed");
    expect(snapTarget(620, 1500, h)).toBe("collapsed");
  });

  test("below fling speed the position still decides", () => {
    expect(snapTarget(280, -(FLING_VELOCITY - 1), h)).toBe("collapsed");
    expect(snapTarget(620, FLING_VELOCITY - 1, h)).toBe("expanded");
  });

  test("nothing to expand into: always collapsed", () => {
    expect(snapTarget(700, -3000, sheetHeights(700, 640))).toBe("collapsed");
  });
});
