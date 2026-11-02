import { createTripLog, MAX_BATCH, MAX_FAILURES, MAX_PENDING, newTripId, type TripEvent } from "../net/tripEvents";

class Refused extends Error {}

function harness(opts: { online?: boolean; post?: (events: TripEvent[]) => Promise<unknown> } = {}) {
  let now = 1_700_000_000_000;
  let saved: TripEvent[] = [];
  const posts: TripEvent[][] = [];
  const timers: { fn: () => void }[] = [];
  const log = createTripLog({
    post:
      opts.post ??
      (async (events) => {
        posts.push(events);
      }),
    isRefused: (e) => e instanceof Refused,
    online: () => opts.online ?? true,
    now: () => now,
    load: async () => saved,
    save: async (events) => {
      saved = [...events];
    },
    setTimer: (fn) => {
      const t = { fn };
      timers.push(t);
      return t;
    },
    clearTimer: (id) => {
      const i = timers.indexOf(id as { fn: () => void });
      if (i >= 0) timers.splice(i, 1);
    },
  });
  const runTimers = () => {
    const due = timers.splice(0);
    due.forEach((t) => t.fn());
  };
  return { log, posts, runTimers, saved: () => saved, setSaved: (e: TripEvent[]) => (saved = e), tick: (ms: number) => (now += ms) };
}

test("newTripId is unique-looking and readable", () => {
  const a = newTripId(1_700_000_000_000, () => 0.5);
  expect(a).toMatch(/^trip-[0-9a-z]+-[0-9a-z]{4}$/);
  expect(newTripId(1_700_000_000_000, () => 0.1)).not.toBe(a);
});

test("events are stamped, tied to the trip and sent oldest first", async () => {
  const h = harness();
  h.log.log("route_started", { place_id: "p1" }, "trip-a");
  h.tick(1000);
  h.log.log("maneuver_said", { to_next_m: 40 }, "trip-a");
  h.log.log("app_opened"); // no trip
  expect(h.log.size()).toBe(3);
  expect(await h.log.flush()).toBe("sent");
  expect(h.posts).toHaveLength(1);
  expect(h.posts[0].map((e) => e.kind)).toEqual(["route_started", "maneuver_said", "app_opened"]);
  expect(h.posts[0][0]).toMatchObject({ trip_id: "trip-a", t: "2023-11-14T22:13:20.000Z", data: { place_id: "p1" } });
  expect(h.posts[0][2].trip_id).toBeUndefined();
  expect(h.log.size()).toBe(0);
  expect(h.log.lastFlushAt()).toBe(1_700_000_001_000);
  expect(await h.log.flush()).toBe("empty");
});

test("offline keeps the backlog; the mirror in storage follows it", async () => {
  const h = harness({ online: false });
  h.log.log("reroute", { cause: "off_route" }, "trip-a");
  expect(await h.log.flush()).toBe("offline");
  expect(h.log.size()).toBe(1);
  h.runTimers(); // the debounced save
  expect(h.saved()).toHaveLength(1);
});

test("a transport failure keeps the batch; the third in a row drops it", async () => {
  let fail = true;
  const h = harness({
    post: async () => {
      if (fail) throw new Error("network");
    },
  });
  h.log.log("a", {}, "t");
  h.log.log("b", {}, "t");
  for (let i = 1; i < MAX_FAILURES; i++) {
    expect(await h.log.flush()).toBe("kept");
    expect(h.log.size()).toBe(2);
  }
  expect(await h.log.flush()).toBe("dropped");
  expect(h.log.size()).toBe(0);
  fail = false;
  h.log.log("c", {}, "t");
  expect(await h.log.flush()).toBe("sent"); // the count restarted
});

test("a refused batch (4xx) is dropped at once and does not count as a failure", async () => {
  const h = harness({
    post: async () => {
      throw new Refused("422");
    },
  });
  h.log.log("bad kind", {}, "t");
  expect(await h.log.flush()).toBe("dropped");
  expect(h.log.size()).toBe(0);
});

test("batches are capped and the backlog is capped", async () => {
  const h = harness();
  for (let i = 0; i < MAX_PENDING + 50; i++) h.log.log("x", { i }, "t");
  expect(h.log.size()).toBe(MAX_PENDING);
  expect(await h.log.flush()).toBe("sent");
  expect(h.posts[0]).toHaveLength(MAX_BATCH);
  expect((h.posts[0][0].data as { i: number }).i).toBe(50); // the oldest 50 were dropped, not the newest
  expect(h.log.size()).toBe(MAX_PENDING - MAX_BATCH);
});

test("restore brings back what a killed app left in storage, ahead of new events", async () => {
  const h = harness();
  h.setSaved([{ t: "2023-11-14T22:00:00.000Z", kind: "route_started", data: {}, trip_id: "old" }]);
  h.log.log("route_ended", {}, "old");
  await h.log.restore();
  expect(h.log.size()).toBe(2);
  await h.log.flush();
  expect(h.posts[0].map((e) => e.kind)).toEqual(["route_started", "route_ended"]);
});

test("a flush in flight is not doubled", async () => {
  let release: () => void = () => {};
  const h = harness({ post: () => new Promise<void>((r) => (release = r)) });
  h.log.log("a", {}, "t");
  const first = h.log.flush();
  expect(await h.log.flush()).toBe("busy");
  release();
  expect(await first).toBe("sent");
});
