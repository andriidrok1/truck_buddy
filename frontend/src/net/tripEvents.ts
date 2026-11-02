// Trip events: what happened on a drive (route set, reroutes and why, stops, turns said at the last
// moment, lines the speech queue dropped, turns the copilot did not understand), batched to
// POST /api/trips/events so the next decisions about the navigator come from real drives, not from
// the emulator. Telemetry never gets in the way of navigation: the buffer lives in memory with a
// mirror in storage (a killed app keeps its tail), a flush is one request a minute and one more on
// End route, nothing is retried in a hurry, and a backlog that will not drain is dropped.

import { useSyncExternalStore } from "react";

import { ApiError, api } from "../api";
import { getLocation } from "../geo/location";
import { isOnline } from "../hooks/useOnline";
import { getSim } from "../sim";
import { storage } from "../utils/storage";

export type TripEvent = {
  /** Event time, ISO 8601 (the phone's clock). */
  t: string;
  trip_id?: string;
  /** ^[a-z][a-z0-9_]{0,39}$ on the backend. */
  kind: string;
  data: Record<string, unknown>;
};

export const PENDING_KEY = "trip_events_pending";
/** One request a minute while something is pending. */
export const FLUSH_EVERY_MS = 60_000;
/** Events per request (the backend's cap). */
export const MAX_BATCH = 200;
/** More than this pending and the oldest go: the phone is not a database. */
export const MAX_PENDING = 500;
/** Transport failures in a row before the batch that keeps failing is dropped. */
export const MAX_FAILURES = 3;
/** Storage writes are coalesced this much (events come in bursts around a turn). */
const PERSIST_DEBOUNCE_MS = 2000;

export type FlushResult = "sent" | "empty" | "offline" | "busy" | "kept" | "dropped";

export type TripLogDeps = {
  /** Sends one batch; resolves on 2xx, rejects otherwise. */
  post: (events: TripEvent[]) => Promise<unknown>;
  /** A rejection that means "the server refused this batch" (4xx): retrying would not help. */
  isRefused: (e: unknown) => boolean;
  online: () => boolean;
  now: () => number;
  load: () => Promise<TripEvent[]>;
  save: (events: TripEvent[]) => Promise<unknown>;
  /** Where the truck is (a real GPS fix only); stamped on every event that does not carry its own, so a
   *  fleet dashboard can show the truck's last position. */
  position?: () => { lat: number; lng: number } | null;
  /** setTimeout/clearTimeout stand-ins, for tests. */
  setTimer?: (fn: () => void, ms: number) => unknown;
  clearTimer?: (id: unknown) => void;
};

export type TripLog = {
  /** Queues one event; `tripId` ties it to the drive (omitted for app-level events). */
  log(kind: string, data?: Record<string, unknown>, tripId?: string | null): void;
  /** Sends the oldest batch when online and something is pending. */
  flush(): Promise<FlushResult>;
  /** Loads the mirror written before a restart (call once at startup). */
  restore(): Promise<void>;
  /** Pending events (for the tester card). */
  size(): number;
  /** When the last batch was accepted, epoch ms, or null. */
  lastFlushAt(): number | null;
  subscribe(listener: () => void): () => void;
  /** Every logged event, as it is logged (the drive recorder marks them on its timeline). */
  onEvent(listener: (event: TripEvent) => void): () => void;
};

/** `trip-<base36 time>-<4 random>`: unique enough per device, readable in a log. */
export function newTripId(now = Date.now(), rand = Math.random): string {
  const tail = Math.floor(rand() * 36 ** 4)
    .toString(36)
    .padStart(4, "0");
  return `trip-${now.toString(36)}-${tail}`;
}

export function createTripLog(deps: TripLogDeps): TripLog {
  const setTimer = deps.setTimer ?? ((fn, ms) => setTimeout(fn, ms));
  const clearTimer = deps.clearTimer ?? ((id) => clearTimeout(id as ReturnType<typeof setTimeout>));
  let pending: TripEvent[] = [];
  let failures = 0;
  let flushing = false;
  let lastFlush: number | null = null;
  let persistTimer: unknown = null;
  const listeners = new Set<() => void>();
  const eventListeners = new Set<(event: TripEvent) => void>();
  const notify = () => listeners.forEach((l) => l());

  const persist = () => {
    if (persistTimer) clearTimer(persistTimer);
    persistTimer = setTimer(() => {
      persistTimer = null;
      deps.save(pending).catch(() => {});
    }, PERSIST_DEBOUNCE_MS);
  };

  return {
    log(kind, data = {}, tripId) {
      const at = data.lat === undefined ? deps.position?.() : null;
      if (at) data = { ...data, lat: Math.round(at.lat * 1e5) / 1e5, lng: Math.round(at.lng * 1e5) / 1e5 };
      const ev: TripEvent = { t: new Date(deps.now()).toISOString(), kind, data };
      if (tripId) ev.trip_id = tripId;
      pending.push(ev);
      if (pending.length > MAX_PENDING) pending = pending.slice(pending.length - MAX_PENDING);
      persist();
      notify();
      eventListeners.forEach((l) => l(ev));
    },
    async flush() {
      if (!pending.length) return "empty";
      if (!deps.online()) return "offline";
      if (flushing) return "busy";
      flushing = true;
      const batch = pending.slice(0, MAX_BATCH);
      try {
        await deps.post(batch);
        pending = pending.slice(batch.length);
        failures = 0;
        lastFlush = deps.now();
        persist();
        notify();
        return "sent";
      } catch (e) {
        if (deps.isRefused(e)) {
          // The server will never take this batch (shape, size): nothing to wait for.
          pending = pending.slice(batch.length);
          failures = 0;
          persist();
          notify();
          return "dropped";
        }
        failures += 1;
        if (failures >= MAX_FAILURES) {
          // Three misses in a row: the link or the server is down for a while. Keep what came after
          // the batch (it is newer and smaller), let this batch go, and count from zero.
          pending = pending.slice(batch.length);
          failures = 0;
          persist();
          notify();
          return "dropped";
        }
        return "kept";
      } finally {
        flushing = false;
      }
    },
    async restore() {
      const saved = await deps.load();
      if (saved.length) {
        pending = [...saved, ...pending].slice(-MAX_PENDING);
        notify();
      }
    },
    size: () => pending.length,
    lastFlushAt: () => lastFlush,
    subscribe(listener) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    onEvent(listener) {
      eventListeners.add(listener);
      return () => {
        eventListeners.delete(listener);
      };
    },
  };
}

const isEventList = (v: unknown): v is TripEvent[] =>
  Array.isArray(v) && v.every((e) => e && typeof e === "object" && typeof (e as TripEvent).kind === "string" && typeof (e as TripEvent).t === "string");

/** The app's one trip log. */
export const tripLog: TripLog = createTripLog({
  post: (events) => api.tripEvents(events),
  isRefused: (e) => e instanceof ApiError && e.status >= 400 && e.status < 500,
  online: isOnline,
  now: Date.now,
  position: () => {
    const sim = getSim(); // a tester's spot or replayed trace is where the truck is, as useDriverLocation has it
    if (sim) return sim.coords;
    const l = getLocation();
    return l.isDemo ? null : l.coords;
  },
  load: async () => {
    const raw = await storage.getItem<TripEvent[] | null>(PENDING_KEY, null);
    return isEventList(raw) ? raw : [];
  },
  save: (events) => (events.length ? storage.setItem(PENDING_KEY, events as unknown as Record<string, unknown>[]) : storage.removeItem(PENDING_KEY)),
});

let ticker: ReturnType<typeof setInterval> | null = null;

/** Restores the mirror and flushes once a minute; `stopTripLog` for the screen's unmount. */
export function startTripLog(): void {
  if (ticker) return;
  tripLog.restore().catch(() => {});
  ticker = setInterval(() => {
    tripLog.flush().catch(() => {});
  }, FLUSH_EVERY_MS);
}

export function stopTripLog(): void {
  if (ticker) clearInterval(ticker);
  ticker = null;
}

const snapshot = () => `${tripLog.size()}:${tripLog.lastFlushAt() ?? ""}`;

/** Pending count and the last accepted flush, for the tester card. */
export function useTripLog(): { pending: number; lastFlushAt: number | null } {
  useSyncExternalStore(tripLog.subscribe, snapshot, snapshot);
  return { pending: tripLog.size(), lastFlushAt: tripLog.lastFlushAt() };
}
