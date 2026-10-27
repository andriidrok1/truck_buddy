import { AMBIENT_GAP_MS, isAmbient, preempts, PRIORITY, SpeechQueue, type DropReason, type QueuedSpeech } from "../voice/queue";

const T0 = 1_700_000_000_000;
const maneuver = (text: string, key?: string) => ({ text, priority: PRIORITY.MANEUVER, key, ttlMs: 30000, kind: "maneuver" as const });
const now = (text: string, key?: string) => ({ text, priority: PRIORITY.MANEUVER_NOW, key, ttlMs: 8000, kind: "maneuver" as const });
const weather = (text: string) => ({ text, priority: PRIORITY.WEATHER, kind: "ambient" as const });
const speed = (text: string) => ({ text, priority: PRIORITY.SPEED, kind: "ambient" as const });
const reviews = (text: string) => ({ text, priority: PRIORITY.REVIEWS, kind: "ambient" as const });

/** Takes everything available at `t`, in order. */
const drain = (q: SpeechQueue, t: number) => {
  const out: string[] = [];
  for (let item = q.take(t, false); item; item = q.take(t, false)) out.push(item.text);
  return out;
};

describe("priorities", () => {
  test("ambient starts at weather", () => {
    expect(isAmbient(PRIORITY.MANEUVER_NOW)).toBe(false);
    expect(isAmbient(PRIORITY.MANEUVER)).toBe(false);
    expect(isAmbient(PRIORITY.CLOSURE_CHAIN)).toBe(false);
    expect(isAmbient(PRIORITY.WEATHER)).toBe(true);
    expect(isAmbient(PRIORITY.SPEED)).toBe(true);
    expect(isAmbient(PRIORITY.REVIEWS)).toBe(true);
  });
});

describe("preempts", () => {
  test("a due turn cuts anything but another due turn", () => {
    expect(preempts(maneuver("far"), now("turn"))).toBe(true);
    expect(preempts(weather("w"), now("turn"))).toBe(true);
    expect(preempts({ priority: PRIORITY.CLOSURE_CHAIN }, now("turn"))).toBe(true);
    expect(preempts(now("a"), now("b"))).toBe(false);
  });

  test("maneuver calls and closures cut ambient lines only", () => {
    expect(preempts(reviews("r"), maneuver("m"))).toBe(true);
    expect(preempts(speed("s"), { priority: PRIORITY.CLOSURE_CHAIN })).toBe(true);
    expect(preempts(maneuver("a"), maneuver("b"))).toBe(false);
    expect(preempts(now("a"), maneuver("b"))).toBe(false);
  });

  test("ambient never cuts anything", () => {
    expect(preempts(reviews("r"), weather("w"))).toBe(false);
    expect(preempts(maneuver("m"), speed("s"))).toBe(false);
    expect(preempts(weather("a"), weather("b"))).toBe(false);
  });
});

describe("SpeechQueue", () => {
  test("priority first, then FIFO", () => {
    const q = new SpeechQueue();
    q.enqueue(reviews("reviews"), T0);
    q.enqueue(maneuver("far 1"), T0 + 1);
    q.enqueue(weather("weather"), T0 + 2);
    q.enqueue(maneuver("far 2"), T0 + 3);
    q.enqueue(now("turn"), T0 + 4);
    q.enqueue({ text: "closure", priority: PRIORITY.CLOSURE_CHAIN, kind: "system" }, T0 + 5);
    expect(q.pending()).toBe(6);
    expect(q.take(T0 + 10, false)?.text).toBe("turn");
    expect(q.take(T0 + 10, false)?.text).toBe("far 1");
    expect(q.take(T0 + 10, false)?.text).toBe("far 2");
    expect(q.take(T0 + 10, false)?.text).toBe("closure");
    expect(q.take(T0 + 10, false)?.text).toBe("weather");
    expect(q.take(T0 + 10, false)).toBeNull(); // reviews: ambient minute not over
    expect(q.pending()).toBe(1);
    expect(q.take(T0 + 10 + AMBIENT_GAP_MS, false)?.text).toBe("reviews");
    expect(q.take(T0 + 10 + AMBIENT_GAP_MS, false)).toBeNull();
  });

  test("null while busy, items stay", () => {
    const q = new SpeechQueue();
    q.enqueue(now("turn"), T0);
    expect(q.take(T0, true)).toBeNull();
    expect(q.pending()).toBe(1);
    expect(q.take(T0 + 100, false)?.text).toBe("turn");
    expect(q.take(T0 + 100, false)).toBeNull();
  });

  test("ambient: at most one per 60 s, urgent lines unaffected", () => {
    const q = new SpeechQueue();
    q.enqueue(weather("w1"), T0);
    q.enqueue(speed("s1"), T0);
    expect(drain(q, T0)).toEqual(["w1"]);
    q.enqueue(maneuver("m"), T0 + 1000);
    expect(drain(q, T0 + 1000)).toEqual(["m"]); // s1 still waits
    expect(drain(q, T0 + AMBIENT_GAP_MS - 1)).toEqual([]);
    expect(drain(q, T0 + AMBIENT_GAP_MS)).toEqual(["s1"]);
    q.enqueue(reviews("r"), T0 + AMBIENT_GAP_MS);
    expect(drain(q, T0 + AMBIENT_GAP_MS + 1000)).toEqual([]);
    expect(drain(q, T0 + 2 * AMBIENT_GAP_MS)).toEqual(["r"]);
  });

  test("same key replaces the older line (near call replaces an unplayed far call)", () => {
    const drops: [string, DropReason][] = [];
    const q = new SpeechQueue({ onDrop: (i, why) => drops.push([i.text, why]) });
    q.enqueue(maneuver("In about a mile, turn right.", "m-3"), T0);
    q.enqueue(maneuver("In half a mile, turn right.", "m-3"), T0 + 20000);
    q.enqueue(now("Turn right.", "m-3-now"), T0 + 40000);
    expect(q.pending()).toBe(2);
    expect(drops).toEqual([["In about a mile, turn right.", "replaced"]]);
    expect(drain(q, T0 + 40000)).toEqual(["Turn right.", "In half a mile, turn right."]);
  });

  test("ttl: stale lines are dropped at take, even while busy", () => {
    const drops: [string, DropReason][] = [];
    const q = new SpeechQueue({ onDrop: (i, why) => drops.push([i.text, why]) });
    q.enqueue(now("Turn right.", "m-1-now"), T0);
    q.enqueue(maneuver("In half a mile, keep left.", "m-2"), T0);
    q.enqueue(weather("Snow ahead."), T0); // no ttl: never expires
    expect(q.take(T0 + 7999, true)).toBeNull();
    expect(q.pending()).toBe(3);
    expect(q.take(T0 + 8000, true)).toBeNull();
    expect(q.pending()).toBe(2);
    expect(drops).toEqual([["Turn right.", "expired"]]);
    expect(drain(q, T0 + 30000)).toEqual(["Snow ahead."]);
    expect(drops).toHaveLength(2);
    expect(drops[1]).toEqual(["In half a mile, keep left.", "expired"]);
  });

  test("clear drops everything", () => {
    const drops: DropReason[] = [];
    const q = new SpeechQueue({ onDrop: (_, why) => drops.push(why) });
    q.enqueue(maneuver("a"), T0);
    q.enqueue(reviews("b"), T0);
    q.clear();
    expect(q.pending()).toBe(0);
    expect(drops).toEqual(["cleared", "cleared"]);
    expect(q.take(T0, false)).toBeNull();
  });

  test("replay: what a drive past a closure in the snow sounds like", () => {
    // Lines arrive as the trace plays; the "player" takes one line per 3 s tick when free.
    const q = new SpeechQueue();
    const events: [number, Parameters<SpeechQueue["enqueue"]>[0]][] = [
      [0, maneuver("In about a mile, take exit 40.", "m-5")],
      [1, weather("Winter weather advisory for the next 20 miles.")],
      [2, speed("You're doing 70 in a 55.")],
      [30, maneuver("In half a mile, take exit 40.", "m-5")],
      [31, reviews("Drivers say the lot at Pilot fills up after 8.")],
      [55, now("Take exit 40.", "m-5-now")],
      [56, { text: "Road closed ahead on US-50, taking I-80 instead.", priority: PRIORITY.CLOSURE_CHAIN, kind: "system" as const }],
    ];
    const spoken: [number, string][] = [];
    let busyUntil = -1;
    for (let s = 0; s <= 130; s++) {
      const t = T0 + s * 1000;
      events.filter(([at]) => at === s).forEach(([, item]) => q.enqueue(item, t));
      if (s < busyUntil) continue;
      const item = q.take(t, false);
      if (item) {
        spoken.push([s, item.text]);
        busyUntil = s + 3;
      }
    }
    expect(spoken).toEqual([
      [0, "In about a mile, take exit 40."],
      [3, "Winter weather advisory for the next 20 miles."],
      // "70 in a 55" waits for the ambient minute (t=63) and is not dropped (no ttl).
      [30, "In half a mile, take exit 40."],
      [55, "Take exit 40."],
      [58, "Road closed ahead on US-50, taking I-80 instead."],
      [63, "You're doing 70 in a 55."],
      [123, "Drivers say the lot at Pilot fills up after 8."],
    ]);
    expect(q.pending()).toBe(0);
  });

  test("enqueue returns the queued line with its order", () => {
    const q = new SpeechQueue();
    const a: QueuedSpeech = q.enqueue(maneuver("a"), T0);
    const b = q.enqueue(maneuver("b"), T0);
    expect(a.at).toBe(T0);
    expect(b.seq).toBe(a.seq + 1);
  });
});
