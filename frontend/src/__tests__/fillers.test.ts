import { pickFiller, planFiller } from "../voice/fillers";

const CLIPS = ["Hang on.", "On it.", "Checking.", "Okay, one sec."];
const SECOND = "Still looking.";

/** Lets the promise chains inside the plan settle between timer steps. */
const flush = async () => {
  for (let i = 0; i < 10; i++) await Promise.resolve();
};

function plan(over: Partial<Parameters<typeof planFiller>[0]> = {}) {
  const played: { clip: string; at: number }[] = [];
  const play = jest.fn((clip: string) => {
    played.push({ clip, at: Date.now() });
    return Promise.resolve();
  });
  const p = planFiller({ clips: CLIPS, play, secondClip: SECOND, skipRate: 0, random: () => 0, ...over });
  return { p, play, played };
}

describe("planFiller", () => {
  beforeEach(() => {
    jest.useFakeTimers({ now: 1_000_000 });
  });
  afterEach(() => {
    jest.useRealTimers();
  });

  test("the first clip plays at 300 ms", () => {
    const { p, play, played } = plan();
    expect(p.picked).toBe("Hang on.");
    jest.advanceTimersByTime(299);
    expect(play).not.toHaveBeenCalled();
    jest.advanceTimersByTime(1);
    expect(played).toEqual([{ clip: "Hang on.", at: 1_000_300 }]);
  });

  test("the second clip plays once at 4300 ms when nothing cancelled it, then done resolves", async () => {
    const { p, played } = plan();
    let done = false;
    p.done.then(() => (done = true));
    jest.advanceTimersByTime(4299);
    await flush();
    expect(played.map((x) => x.clip)).toEqual(["Hang on."]);
    expect(done).toBe(false);
    jest.advanceTimersByTime(1);
    await flush();
    expect(played).toEqual([
      { clip: "Hang on.", at: 1_000_300 },
      { clip: SECOND, at: 1_004_300 },
    ]);
    expect(done).toBe(true);
    jest.advanceTimersByTime(10000);
    await flush();
    expect(played).toHaveLength(2);
  });

  test("cancel at 100 ms: nothing plays and done resolves", async () => {
    const { p, play } = plan();
    jest.advanceTimersByTime(100);
    expect(p.cancel()).toBe(true);
    await expect(p.done).resolves.toBeUndefined();
    jest.advanceTimersByTime(10000);
    await flush();
    expect(play).not.toHaveBeenCalled();
  });

  test("cancel at 1000 ms: the first clip only", async () => {
    const { p, play, played } = plan();
    jest.advanceTimersByTime(1000);
    await flush();
    expect(p.cancel()).toBe(false);
    await expect(p.done).resolves.toBeUndefined();
    jest.advanceTimersByTime(10000);
    await flush();
    expect(played.map((x) => x.clip)).toEqual(["Hang on."]);
    expect(play).toHaveBeenCalledTimes(1);
  });

  test("cancel while the first clip is still playing: done waits for it, no second clip", async () => {
    let finish: () => void = () => {};
    const play = jest.fn(() => new Promise<void>((r) => (finish = r)));
    const p = planFiller({ clips: CLIPS, play, secondClip: SECOND, random: () => 0 });
    jest.advanceTimersByTime(300);
    let done = false;
    p.done.then(() => (done = true));
    p.cancel();
    await flush();
    expect(done).toBe(false);
    finish();
    await flush();
    expect(done).toBe(true);
    expect(play).toHaveBeenCalledTimes(1);
  });

  test("skips the turn when random is below skipRate", async () => {
    const { p, play } = plan({ skipRate: 0.25, random: () => 0.2 });
    expect(p.picked).toBeNull();
    await expect(p.done).resolves.toBeUndefined();
    jest.advanceTimersByTime(10000);
    await flush();
    expect(play).not.toHaveBeenCalled();
  });

  test("plays when random is at or above skipRate", () => {
    const { p } = plan({ skipRate: 0.25, random: () => 0.25 });
    expect(p.picked).not.toBeNull();
  });

  test("never repeats lastClip", () => {
    const { p } = plan({ lastClip: "Hang on." });
    expect(p.picked).toBe("On it.");
    for (let k = 0; k < 50; k++) {
      const r = k / 50;
      expect(pickFiller(CLIPS, "Hang on.", () => r)).not.toBe("Hang on.");
    }
  });

  test("no clips: nothing is planned", async () => {
    const { p, play } = plan({ clips: [] });
    expect(p.picked).toBeNull();
    jest.advanceTimersByTime(10000);
    await flush();
    expect(play).not.toHaveBeenCalled();
  });

  test("without a second clip, done resolves when the first finishes", async () => {
    const { p, play } = plan({ secondClip: null });
    jest.advanceTimersByTime(300);
    await expect(p.done).resolves.toBeUndefined();
    jest.advanceTimersByTime(10000);
    await flush();
    expect(play).toHaveBeenCalledTimes(1);
  });
});

describe("pickFiller", () => {
  test("random**2 biases towards the short clips at the top", () => {
    expect(pickFiller(CLIPS, null, () => 0)).toBe("Hang on.");
    expect(pickFiller(CLIPS, null, () => 0.49)).toBe("Hang on."); // 4 * 0.49**2 = 0.96 -> index 0
    expect(pickFiller(CLIPS, null, () => 0.5)).toBe("On it."); // 4 * 0.25 = 1 -> index 1
    expect(pickFiller(CLIPS, null, () => 0.99)).toBe("Okay, one sec.");
    expect(pickFiller([], null)).toBeNull();
  });
});

describe("planFiller timing report", () => {
  beforeEach(() => {
    jest.useFakeTimers({ now: 1_000_000 });
  });
  afterEach(() => {
    jest.useRealTimers();
  });

  test("startedAt is ms after the plan once the first clip plays; elapsed follows the clock", () => {
    const p = planFiller({ clips: CLIPS, play: () => Promise.resolve(), random: () => 0 });
    expect(p.startedAt()).toBeNull();
    jest.advanceTimersByTime(300);
    expect(p.startedAt()).toBe(300);
    jest.advanceTimersByTime(900);
    expect(p.elapsed()).toBe(1200);
  });

  test("a cancelled or skipped plan never reports a start", () => {
    const a = planFiller({ clips: CLIPS, play: () => Promise.resolve(), random: () => 0 });
    jest.advanceTimersByTime(100);
    a.cancel();
    jest.advanceTimersByTime(1000);
    expect(a.startedAt()).toBeNull();
    const b = planFiller({ clips: CLIPS, play: () => Promise.resolve(), skipRate: 1, random: () => 0.5 });
    jest.advanceTimersByTime(1000);
    expect(b.startedAt()).toBeNull();
    expect(b.elapsed()).toBe(1000);
  });
});
