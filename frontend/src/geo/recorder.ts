// Drive recorder: the real GPS fixes of a drive as a TracePoint[] (the same shape the tester's synthetic
// traces use), with the route the truck was following, every route the app swapped in (reroute,
// alternative, stop) and marks for what the copilot said and logged. A recording replays through
// playTrace() with the route it was made on, and once copied into fixtures/traces/ it drives the
// guidance regression in CI. Pure core with injected clock and writer; the file side is traceFiles.ts.

import type { DriverLocation } from "./location";
import type { TracePoint } from "./trace";
import type { Place, RouteInfo } from "../types";

export const TRACE_FORMAT_VERSION = 1;
/** The file on disk is rewritten this often while recording, and once more on stop. */
export const WRITE_EVERY_MS = 30_000;

export type TraceMark = { t_ms: number; kind: string; text: string };

export type RecordedTrace = {
  version: typeof TRACE_FORMAT_VERSION;
  /** ISO time of the first fix. */
  started_at: string;
  trip_id: string | null;
  /** The destination, enough to start the route again on replay. */
  place: Place | null;
  /** The route when recording began. */
  route: RouteInfo | null;
  /** Every route the app swapped in afterwards (reroute, alternative, stop), with the recording time. */
  route_changes: { t_ms: number; route: RouteInfo }[];
  fixes: TracePoint[];
  marks: TraceMark[];
};

export type RecorderDeps = {
  subscribe: (listener: () => void) => () => void;
  getLocation: () => DriverLocation;
  now: () => number;
  /** Writes the whole recording; errors are the writer's problem (logged there), never the recorder's. */
  write: (name: string, trace: RecordedTrace) => Promise<void>;
  setTimer?: (fn: () => void, ms: number) => unknown;
  clearTimer?: (id: unknown) => void;
};

export type Recorder = {
  start(args: { tripId?: string | null; place?: Place | null; route?: RouteInfo | null }): void;
  /** Writes the last time and returns the file name, or null when nothing was recording. */
  stop(): Promise<string | null>;
  isRecording(): boolean;
  /** The app swapped the active route (reroute, alternative, stop added): remembered with the time. */
  routeChanged(route: RouteInfo): void;
  /** Something worth seeing next to the fixes: a spoken line, a trip event, a tester note. */
  mark(kind: string, text: string): void;
  /** The recording so far (a copy), or null. */
  current(): RecordedTrace | null;
  /** The last file written, for "Share last trace". */
  lastFile(): string | null;
  subscribe(listener: () => void): () => void;
};

/** `2026-11-01-19-30-pilot-fernley.json`: date and time of the first fix, then the destination. */
export function traceFileName(startedAt: number, place: Place | null): string {
  const d = new Date(startedAt);
  const pad = (n: number) => String(n).padStart(2, "0");
  const stamp = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}-${pad(d.getHours())}-${pad(d.getMinutes())}`;
  const slug = (place?.name ?? "drive")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 40);
  return `${stamp}-${slug || "drive"}.json`;
}

/** A GPS fix as a trace point; `t_ms` counts from the first fix of the recording. */
export function toTracePoint(loc: DriverLocation, startTs: number): TracePoint {
  return {
    lat: loc.coords.lat,
    lng: loc.coords.lng,
    heading: loc.heading ?? 0,
    speed: loc.speed ?? 0,
    accuracy: loc.accuracy ?? 5,
    t_ms: Math.max(0, loc.ts - startTs),
  };
}

export function createRecorder(deps: RecorderDeps): Recorder {
  const setTimer = deps.setTimer ?? ((fn, ms) => setTimeout(fn, ms));
  const clearTimer = deps.clearTimer ?? ((id) => clearTimeout(id as ReturnType<typeof setTimeout>));
  let trace: RecordedTrace | null = null;
  let startTs = 0; // the first real fix's timestamp; marks before it count from the start call
  let startedWall = 0;
  let lastFixTs = 0;
  let unsubscribe: (() => void) | null = null;
  let timer: unknown = null;
  let name: string | null = null;
  let last: string | null = null;
  const listeners = new Set<() => void>();
  const notify = () => listeners.forEach((l) => l());

  const tMs = () => (startTs ? Math.max(0, deps.now() - startTs) : Math.max(0, deps.now() - startedWall));

  const onFix = () => {
    const loc = deps.getLocation();
    if (!trace || loc.isDemo || loc.ts <= 0 || loc.ts === lastFixTs) return;
    if (!startTs) {
      startTs = loc.ts;
      trace.started_at = new Date(loc.ts).toISOString();
      name = traceFileName(loc.ts, trace.place);
    }
    lastFixTs = loc.ts;
    trace.fixes.push(toTracePoint(loc, startTs));
  };

  const flush = async () => {
    if (!trace || !name) return;
    await deps.write(name, trace);
    last = name;
    notify();
  };

  const schedule = () => {
    timer = setTimer(() => {
      timer = null;
      flush().catch(() => {});
      if (trace) schedule();
    }, WRITE_EVERY_MS);
  };

  return {
    start({ tripId = null, place = null, route = null }) {
      if (trace) return;
      startedWall = deps.now();
      startTs = 0;
      lastFixTs = 0;
      name = null;
      trace = { version: TRACE_FORMAT_VERSION, started_at: new Date(startedWall).toISOString(), trip_id: tripId, place, route, route_changes: [], fixes: [], marks: [] };
      unsubscribe = deps.subscribe(onFix);
      onFix(); // the fix already in hand
      schedule();
      notify();
    },
    async stop() {
      if (!trace) return null;
      unsubscribe?.();
      unsubscribe = null;
      if (timer) clearTimer(timer);
      timer = null;
      if (!name) name = traceFileName(startedWall, trace.place); // no fix ever came: still keep the marks
      await flush();
      const done = name;
      trace = null;
      name = null;
      notify();
      return done;
    },
    isRecording: () => trace !== null,
    routeChanged(route) {
      if (!trace || trace.route === route) return;
      if (trace.route === null) trace.route = route;
      else trace.route_changes.push({ t_ms: tMs(), route });
    },
    mark(kind, text) {
      if (!trace) return;
      trace.marks.push({ t_ms: tMs(), kind, text });
    },
    current: () => (trace ? { ...trace, fixes: [...trace.fixes], marks: [...trace.marks], route_changes: [...trace.route_changes] } : null),
    lastFile: () => last,
    subscribe(listener) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
  };
}
