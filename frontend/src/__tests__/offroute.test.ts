import { createOffRouteTracker, thresholdM, type OffRouteFix, type OffRouteVerdict } from "../geo/offroute";

const T0 = 1_700_000_000_000;

/** Fix `i` seconds in, `distM` off the line, highway speed, tight accuracy. */
const fix = (i: number, distM: number, over: Partial<OffRouteFix> = {}): OffRouteFix => ({
  distM,
  speed: 28,
  accuracy: 5,
  ts: T0 + i * 1000,
  thresholdM: 50,
  ...over,
});

/** Pushes one fix per second with the given distances and returns the verdicts. */
const run = (tracker = createOffRouteTracker(), dists: number[], from = 0): OffRouteVerdict[] =>
  dists.map((d, i) => tracker.push(fix(from + i, d)));

describe("thresholdM", () => {
  test("posted limit decides: 50 m from 50 mph up, 30 m below", () => {
    expect(thresholdM(65, 30)).toBe(50);
    expect(thresholdM(50, 0)).toBe(50);
    expect(thresholdM(45, 30)).toBe(30);
    expect(thresholdM(25, null)).toBe(30);
  });

  test("without a limit, highway speed (> 20 m/s) widens the band", () => {
    expect(thresholdM(null, 25)).toBe(50);
    expect(thresholdM(null, 20)).toBe(30);
    expect(thresholdM(null, 10)).toBe(30);
    expect(thresholdM(null, null)).toBe(30);
  });
});

describe("createOffRouteTracker", () => {
  test("on the line is 'on'; three consecutive fixes over the threshold give one 'off'", () => {
    const t = createOffRouteTracker();
    expect(run(t, [3, 8, 12])).toEqual(["on", "on", "on"]);
    // 10 m/s sideways drift: no jump, counts 1, 2, 3.
    expect(run(t, [60, 70, 80, 90, 100], 3)).toEqual(["skip", "skip", "off", "skip", "skip"]);
  });

  test("an 'on' fix in between resets the count", () => {
    const t = createOffRouteTracker();
    expect(run(t, [60, 70, 10, 60, 70, 80])).toEqual(["skip", "skip", "on", "skip", "skip", "off"]);
  });

  test("cooldown: no second 'off' for 30 s, then one more without three new fixes", () => {
    const t = createOffRouteTracker();
    // 40 fixes 60..~70 m off: off at #2 (0-based), nothing until 30 s later.
    const verdicts = run(t, Array.from({ length: 40 }, (_, i) => 60 + (i % 5)));
    const offs = verdicts.map((v, i) => (v === "off" ? i : -1)).filter((i) => i >= 0);
    expect(offs).toEqual([2, 32]);
  });

  test("reset() forgets the count and the cooldown", () => {
    const t = createOffRouteTracker();
    expect(run(t, [60, 70, 80])).toEqual(["skip", "skip", "off"]);
    t.reset();
    expect(run(t, [60, 70, 80], 3)).toEqual(["skip", "skip", "off"]); // no cooldown left
    t.reset();
    expect(run(t, [60, 70], 6)).toEqual(["skip", "skip"]);
    t.reset();
    expect(run(t, [80], 8)).toEqual(["skip"]); // count started over
  });

  test("poor accuracy is skipped and does not count", () => {
    const t = createOffRouteTracker();
    expect(t.push(fix(0, 100, { accuracy: 80 }))).toBe("skip");
    expect(t.push(fix(1, 100, { accuracy: 41 }))).toBe("skip");
    expect(run(t, [100, 100, 100], 2)).toEqual(["skip", "skip", "off"]);
    expect(t.push(fix(5, 100, { accuracy: null }))).toBe("skip"); // unknown accuracy counts as good (cooldown now)
  });

  test("a parked truck is never off, whatever the distance", () => {
    const t = createOffRouteTracker();
    expect(run(t, [200, 200, 200, 200].map((d) => d), 0).length).toBe(4);
    const parked = createOffRouteTracker();
    for (let i = 0; i < 10; i++) expect(parked.push(fix(i, 200, { speed: 0 }))).toBe("skip");
    expect(parked.push(fix(10, 200, { speed: 1.9 }))).toBe("skip");
    // Pulling out again: the parked fixes kept the clock, so this is not a gap; counting starts now.
    expect(run(parked, [200, 200, 200], 11)).toEqual(["skip", "skip", "off"]);
  });

  test("speed null counts as moving", () => {
    const t = createOffRouteTracker();
    expect(run(t, [60, 70, 80]).map((v) => v)).toEqual(["skip", "skip", "off"]);
    const nul = createOffRouteTracker();
    expect([60, 70, 80].map((d, i) => nul.push(fix(i, d, { speed: null })))).toEqual(["skip", "skip", "off"]);
  });

  test("a GPS jump is skipped once; the next fix from the new spot counts", () => {
    const t = createOffRouteTracker();
    expect(run(t, [5, 5])).toEqual(["on", "on"]);
    // 5 m -> 300 m in one second = 295 m/s: a jump. Back to 5 m is a jump again, then forgotten.
    expect(t.push(fix(2, 300))).toBe("skip");
    expect(t.push(fix(3, 5))).toBe("skip");
    expect(t.push(fix(4, 5))).toBe("on");
    // A real teleport (replay detour) sticks: skip, then 1, 2, 3.
    expect(run(t, [300, 305, 310, 315], 5)).toEqual(["skip", "skip", "skip", "off"]);
  });

  test("a gap over 15 s resets the count and is itself skipped", () => {
    const t = createOffRouteTracker();
    expect(run(t, [60, 70])).toEqual(["skip", "skip"]);
    expect(t.push(fix(20, 80))).toBe("skip"); // 18 s later: tunnel / app suspended
    expect(run(t, [80, 80], 21)).toEqual(["skip", "skip"]); // count 1, 2
    expect(t.push(fix(23, 80))).toBe("off");
  });

  test("the same fix pushed twice replays its verdict without counting", () => {
    const t = createOffRouteTracker();
    expect(t.push(fix(0, 60))).toBe("skip");
    expect(t.push(fix(1, 70))).toBe("skip");
    expect(t.push(fix(1, 70))).toBe("skip");
    expect(t.push(fix(1, 70))).toBe("skip");
    expect(t.push(fix(2, 80))).toBe("off");
    expect(t.push(fix(2, 80))).toBe("off"); // the verdict is replayed, not re-judged into the cooldown
    expect(t.push(fix(3, 80))).toBe("skip");
  });

  test("custom options", () => {
    const t = createOffRouteTracker({ need: 1, cooldownMs: 1500 });
    expect(run(t, [60, 60, 60, 60])).toEqual(["off", "skip", "off", "skip"]);
  });
});
