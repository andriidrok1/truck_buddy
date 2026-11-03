import type { DriverLocation } from "../geo/location";
import { createRecorder, toTracePoint, traceFileName, WRITE_EVERY_MS, type RecordedTrace } from "../geo/recorder";
import type { Place, RouteInfo } from "../types";

const place: Place = { id: "p1", name: "Pilot Travel Center, Fernley", category: "truck_stop", lat: 39.6, lng: -119.25, tags: [] };
const route = (summary: string): RouteInfo => ({
  geometry: [
    [38.58, -121.49],
    [38.6, -121.4],
  ],
  distance_m: 8000, duration_s: 600, distance_mi: 5, duration_min: 10, summary, source: "here",
});
const fix = (lat: number, lng: number, ts: number, extra: Partial<DriverLocation> = {}): DriverLocation => ({
  coords: { lat, lng }, heading: 90, speed: 25, accuracy: 6, ts, isDemo: false, resolved: true, ...extra,
});

function harness(first: DriverLocation) {
  let now = 1_700_000_000_000;
  let loc = first;
  const listeners = new Set<() => void>();
  const writes: { name: string; trace: RecordedTrace }[] = [];
  const timers: { fn: () => void }[] = [];
  const rec = createRecorder({
    subscribe: (l) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    getLocation: () => loc,
    now: () => now,
    write: async (name, trace) => {
      writes.push({ name, trace: JSON.parse(JSON.stringify(trace)) });
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
  return {
    rec, writes, timers, listeners,
    push: (next: DriverLocation) => {
      loc = next;
      now = Math.max(now, next.ts);
      listeners.forEach((l) => l());
    },
    tick: (ms: number) => (now += ms),
    fireTimer: () => timers.splice(0).forEach((t) => t.fn()),
  };
}

test("file name: date, time and the destination as a slug", () => {
  const name = traceFileName(new Date(2026, 10, 1, 19, 5).getTime(), place);
  expect(name).toBe("2026-11-01-19-05-pilot-travel-center-fernley.json");
  expect(traceFileName(new Date(2026, 0, 2, 3, 4).getTime(), null)).toBe("2026-01-02-03-04-drive.json");
});

test("a fix becomes a trace point clocked from the first fix", () => {
  expect(toTracePoint(fix(1, 2, 5000, { heading: null, speed: null, accuracy: null }), 2000)).toEqual({
    lat: 1, lng: 2, heading: 0, speed: 0, accuracy: 5, t_ms: 3000,
  });
});

test("records fixes, route changes and marks on one clock; demo and repeated fixes are skipped", async () => {
  const h = harness(fix(38.58, -121.49, 1_700_000_000_000));
  h.rec.start({ tripId: "trip-1", place, route: route("I-80 E") });
  expect(h.rec.isRecording()).toBe(true);
  expect(h.rec.current()?.fixes).toHaveLength(1); // the fix already in hand
  h.push(fix(38.581, -121.489, 1_700_000_001_000));
  h.push(fix(38.581, -121.489, 1_700_000_001_000)); // same ts: not a new fix
  h.push({ ...fix(0, 0, 1_700_000_002_000), isDemo: true }); // the demo point is not a drive
  h.tick(500);
  h.rec.mark("said", "In half a mile, turn right.");
  h.rec.routeChanged(route("I-80 E")); // same object? no, a new one: a swap
  h.push(fix(38.582, -121.488, 1_700_000_003_000));
  const t = h.rec.current()!;
  expect(t.fixes.map((f) => f.t_ms)).toEqual([0, 1000, 3000]);
  expect(t.marks).toEqual([{ t_ms: 2500, kind: "said", text: "In half a mile, turn right." }]); // the demo fix moved the clock to +2 s, then 500 ms
  expect(t.route_changes).toHaveLength(1);
  expect(t.route?.summary).toBe("I-80 E");
  expect(t.trip_id).toBe("trip-1");
  expect(t.started_at).toBe("2023-11-14T22:13:20.000Z");
  const name = await h.rec.stop();
  expect(name).toBe(traceFileName(1_700_000_000_000, place));
  expect(h.writes).toHaveLength(1);
  expect(h.writes[0].trace.fixes).toHaveLength(3);
  expect(h.rec.isRecording()).toBe(false);
  expect(h.rec.lastFile()).toBe(name);
  expect(h.listeners.size).toBe(0); // unsubscribed from the location store
});

test("the file is rewritten every WRITE_EVERY_MS while recording", async () => {
  const h = harness(fix(38.58, -121.49, 1_700_000_000_000));
  h.rec.start({ place, route: route("I-80 E") });
  expect(h.timers).toHaveLength(1);
  h.push(fix(38.59, -121.48, 1_700_000_000_000 + WRITE_EVERY_MS));
  h.fireTimer();
  await Promise.resolve();
  expect(h.writes).toHaveLength(1);
  expect(h.writes[0].trace.fixes).toHaveLength(2);
  expect(h.timers).toHaveLength(1); // rescheduled
  await h.rec.stop();
  expect(h.timers).toHaveLength(0);
});

test("starting on the demo point waits for the first real fix; a second start is ignored", async () => {
  const h = harness({ ...fix(38.58, -121.49, 0), isDemo: true, ts: 0 });
  h.rec.start({ place, route: null });
  h.rec.start({ place: null, route: null });
  expect(h.rec.current()?.fixes).toHaveLength(0);
  expect(h.rec.current()?.place).toEqual(place);
  h.push(fix(38.59, -121.48, 1_700_000_010_000));
  expect(h.rec.current()?.fixes.map((f) => f.t_ms)).toEqual([0]);
  expect(h.rec.current()?.started_at).toBe("2023-11-14T22:13:30.000Z");
  h.rec.routeChanged(route("US-50")); // first route seen after the start fills the gap
  expect(h.rec.current()?.route?.summary).toBe("US-50");
  expect(h.rec.current()?.route_changes).toHaveLength(0);
  await h.rec.stop();
});

test("stopping with no fix at all still writes the marks under the start time", async () => {
  const h = harness({ ...fix(38.58, -121.49, 0), isDemo: true, ts: 0 });
  h.rec.start({ place: null, route: null });
  h.rec.mark("note", "tester tapped record indoors");
  const name = await h.rec.stop();
  expect(name).toMatch(/-drive\.json$/);
  expect(h.writes[0].trace.marks[0].kind).toBe("note");
  expect(await h.rec.stop()).toBeNull();
});
