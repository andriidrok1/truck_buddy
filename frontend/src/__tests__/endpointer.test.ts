import { createEndpointer, MAX_MS, START_TIMEOUT_MS, type Decision } from "../voice/endpointer";

// Level profiles: 50 ms meter polls (useVoice.listen), cab noise with ±2 dB jitter, speech as words (~300 ms at
// the voice level) with short gaps between them that drop back toward the noise.
const POLL = 50;
type Seg = { from: number; to: number; db: number; words?: boolean };

function rng(seed: number) {
  let x = seed;
  return () => {
    x = (x * 1103515245 + 12345) % 2147483648;
    return x / 2147483648;
  };
}

function profile(noise: number, segs: Seg[], seed = 1) {
  const r = rng(seed);
  return (t: number) => {
    const s = segs.find((g) => t >= g.from && t < g.to);
    const jitter = (r() - 0.5) * 4;
    if (!s) return noise + jitter;
    if (s.words && (t - s.from) % 380 >= 300) return noise + 3 + jitter; // gap between words
    return s.db + jitter;
  };
}

function run(level: (t: number) => number, until = MAX_MS + 1000): { decision: Decision; at: number; warned: boolean } {
  const ep = createEndpointer();
  let warned = false;
  for (let t = POLL; t <= until; t += POLL) {
    const d = ep.push(level(t), t);
    if (d === "warn") warned = true;
    else if (d !== "wait") return { decision: d, at: t, warned };
  }
  return { decision: "wait", at: until, warned };
}

test("a phrase in a quiet cab is sent ~1.2 s after it ends", () => {
  const r = run(profile(-55, [{ from: 600, to: 2600, db: -25, words: true }]));
  expect(r.decision).toBe("send");
  expect(r.at).toBeGreaterThanOrEqual(2600 + 1100);
  expect(r.at).toBeLessThanOrEqual(2600 + 1400);
});

test.each([
  ["go", 250],
  ["yes", 280],
  ["no", 200],
])("a short '%s' (%i ms) counts as speech and is sent", (_w, ms) => {
  const r = run(profile(-55, [{ from: 700, to: 700 + (ms as number), db: -25 }]));
  expect(r.decision).toBe("send");
  expect(r.at).toBeLessThan(3000);
});

test("talking from the very first frame (at the chime) still ends on the pause", () => {
  const r = run(profile(-55, [{ from: 0, to: 2500, db: -25, words: true }]));
  expect(r.decision).toBe("send");
  expect(r.at).toBeLessThan(4200);
});

test("highway cab: noise -30 dBFS, voice -22 counts as speech", () => {
  const r = run(profile(-30, [{ from: 800, to: 2800, db: -22, words: true }], 3));
  expect(r.decision).toBe("send");
  expect(r.at).toBeLessThan(4500);
});

test("a voice barely over the highway noise is never thrown away: it goes to the server as maybe", () => {
  const r = run(profile(-30, [{ from: 800, to: 2800, db: -25.5 }], 4));
  expect(["send", "maybe"]).toContain(r.decision);
});

test("a 0.8 s pause inside a phrase does not end it; a 1.5 s one does", () => {
  const short = run(profile(-55, [{ from: 500, to: 1500, db: -25, words: true }, { from: 2300, to: 3300, db: -25, words: true }]));
  expect(short.at).toBeGreaterThan(3300);
  const long = run(profile(-55, [{ from: 500, to: 1500, db: -25, words: true }, { from: 3000, to: 4000, db: -25, words: true }]));
  expect(long.at).toBeLessThan(3000);
});

test("a 25 s story is not cut; the cap sends at 30 s after one warning", () => {
  const story = run(profile(-55, [{ from: 500, to: 25500, db: -25, words: true }]));
  expect(story.decision).toBe("send");
  expect(story.at).toBeGreaterThan(25500);
  expect(story.at).toBeLessThan(MAX_MS);
  const endless = run(profile(-55, [{ from: 500, to: 60000, db: -25, words: true }]));
  expect(endless).toMatchObject({ decision: "send", at: MAX_MS, warned: true });
});

test("silence is silent after the start timeout; a cough is not speech", () => {
  expect(run(profile(-55, []))).toMatchObject({ decision: "silent", at: START_TIMEOUT_MS });
  const cough = run(profile(-55, [{ from: 1000, to: 100 + 1000, db: -20 }]));
  expect(cough.at).toBe(START_TIMEOUT_MS);
  expect(cough.decision).toBe("maybe"); // the server hears it and drops it if there are no words
});

test("decisions are final; stats describe the turn", () => {
  const ep = createEndpointer({ startTimeoutMs: 500 });
  let d: Decision = "wait";
  for (let t = 50; t <= 500; t += 50) d = ep.push(-60, t);
  expect(d).toBe("silent");
  expect(ep.push(-10, 550)).toBe("silent");
  expect(ep.stats()).toMatchObject({ spoke: false, reason: "silent", peakDb: -60 });
});

test("a missing meter value is skipped, not treated as silence", () => {
  const ep = createEndpointer();
  expect(ep.push(Number.NaN, 50)).toBe("wait");
  expect(ep.stats().peakDb).toBe(-160);
});
