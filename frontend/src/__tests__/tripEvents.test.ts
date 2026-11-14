import { createTripLog, MAX_BATCH, MAX_FAILURES, MAX_PENDING, newTripId, POSITION_EVERY_MS, startPositionHeartbeat, type TripEvent } from "../net/tripEvents";

class Refused extends Error {}

function harness(opts: { online?: boolean; post?: (events: TripEvent[]) => Promise<unknown>; position?: () => { lat: number; lng: number } | null } = {}) {
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
    position: opts.position,
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

describe("the same events never go out twice (re-review of #172)", () => {
  /** The reproduction from the review: identity-stamped events, a debounced mirror that writes at once. */
  function stamped() {
    let disk: TripEvent[] = [];
    let who: string | null = "acct-x";
    const posted: TripEvent[] = [];
    const log = createTripLog({
      post: async (b) => {
        posted.push(...b);
      },
      isRefused: () => false,
      online: () => true,
      now: () => 1,
      identity: () => who,
      load: async () => disk,
      save: async (e) => {
        disk = JSON.parse(JSON.stringify(e));
      },
      setTimer: (fn) => {
        fn();
        return 1;
      },
      clearTimer: () => {},
    });
    return { log, posted, disk: () => disk, as: (w: string | null) => (who = w) };
  }

  test("forced sign-out while driving, the same account back without a kill: each held event is posted once", async () => {
    const h = stamped();
    await h.log.restore(); // the first map mount
    h.log.log("stop", {}, "trip-1");
    h.log.log("position", {}, "trip-1");
    await h.log.stash(); // the server signed the driver out, the trip is held
    h.as(null);
    expect(h.log.size()).toBe(0);
    expect(h.disk()).toHaveLength(2);
    h.as("acct-x"); // the same account signs back in
    await h.log.restore(); // settleHeldTrip
    await h.log.restore(); // and the map mounting again
    expect(h.log.size()).toBe(2);
    expect(await h.log.flush()).toBe("sent");
    expect(h.posted.map((e) => e.kind)).toEqual(["stop", "position"]);
    expect(h.posted.every((e) => !("_id" in e) && !("_who" in e))).toBe(true); // the stamps stay on the phone
  });

  test("without a stash too: a second restore of what is already in memory adds nothing", async () => {
    const h = stamped();
    h.log.log("stop", {}, "trip-1"); // written to disk by the mirror at once
    await h.log.restore();
    await h.log.restore();
    expect(h.log.size()).toBe(1);
    await h.log.flush();
    expect(h.posted).toHaveLength(1);
  });

  test("while stashed nothing overwrites the disk copy and nothing is sent; a clear (someone else signed in) drops it", async () => {
    const h = stamped();
    h.log.log("stop", {}, "trip-1");
    await h.log.stash();
    h.as(null);
    h.log.log("app_background", {}); // a screen going away
    expect(h.disk().map((e) => e.kind)).toEqual(["stop"]);
    h.as("acct-y");
    expect(await h.log.flush()).not.toBe("sent");
    expect(h.posted).toEqual([]);
    await h.log.clear();
    expect(h.disk()).toEqual([]);
    await h.log.restore();
    expect(h.log.size()).toBe(0);
  });

  test("events written before ids existed get one on restore, and a duplicate already on disk is sent once", async () => {
    const h = harness();
    const old = { t: "2023-11-14T22:00:00.000Z", kind: "route_started", data: {}, trip_id: "old" };
    h.setSaved([old, { ...old, _id: "e1" } as TripEvent, { ...old, _id: "e1" } as TripEvent]);
    await h.log.restore();
    expect(h.log.size()).toBe(2);
    await h.log.flush();
    expect(h.posts[0]).toHaveLength(2);
  });
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

test("events carry the truck's GPS position unless they bring their own; no fix, no position", async () => {
  let fix: { lat: number; lng: number } | null = { lat: 38.7512345, lng: -121.2054321 };
  const h = harness({ position: () => fix });
  h.log.log("route_started", { place_id: "p" }, "trip-1");
  h.log.log("stop_added", { lat: 39.5, lng: -119.8 }, "trip-1");
  fix = null;
  h.log.log("route_ended", {}, "trip-1");
  await h.log.flush();
  expect(h.posts[0].map((e) => e.data)).toEqual([
    { place_id: "p", lat: 38.75123, lng: -121.20543 },
    { lat: 39.5, lng: -119.8 },
    {},
  ]);
});

describe("startPositionHeartbeat", () => {
  test("one position event every 5 minutes until stopped", () => {
    jest.useFakeTimers();
    const log = { log: jest.fn() };
    const stop = startPositionHeartbeat(log, () => ({ tripId: "t1", speedMps: 26.84 }));
    jest.advanceTimersByTime(POSITION_EVERY_MS - 1);
    expect(log.log).not.toHaveBeenCalled();
    jest.advanceTimersByTime(1);
    expect(log.log).toHaveBeenCalledWith("position", { speed_mps: 26.8 }, "t1");
    jest.advanceTimersByTime(POSITION_EVERY_MS);
    expect(log.log).toHaveBeenCalledTimes(2);
    stop();
    jest.advanceTimersByTime(3 * POSITION_EVERY_MS);
    expect(log.log).toHaveBeenCalledTimes(2);
    jest.useRealTimers();
  });
});
