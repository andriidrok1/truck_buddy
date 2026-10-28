// A failed API call on a link NetInfo still calls "connected" is the usual shape of a dead zone:
// the radio has bars, the packets go nowhere. One network error marks the link offline for a
// short window, so the app behaves as offline between NetInfo events; a call that succeeds
// clears it early. Pure factory + one shared instance (api.ts marks, useOnline reads).

export const OFFLINE_HINT_MS = 20000;

/** fetch's own failures (RN: "Network request failed", web: "Failed to fetch") and our "network" wrapper errors; not a plain abort. */
export function isNetworkError(e: unknown): boolean {
  const s = String(e);
  if (/network|failed to fetch|timed? ?out|ECONN|ENOTFOUND|EAI_AGAIN/i.test(s)) return true;
  return e instanceof TypeError; // fetch rejects with a bare TypeError on some platforms
}

export type OfflineHint = {
  /** A network error just happened: offline until now + windowMs. */
  mark(now: number): void;
  /** A call went through: the link is alive. */
  clear(): void;
  /** Inside the window? */
  active(now: number): boolean;
  /** Epoch ms when the window ends (0 = none). */
  until(): number;
};

export function createOfflineHint(windowMs = OFFLINE_HINT_MS, onChange?: () => void): OfflineHint {
  let until = 0;
  return {
    mark(now) {
      until = now + windowMs;
      onChange?.();
    },
    clear() {
      if (until === 0) return;
      until = 0;
      onChange?.();
    },
    active: (now) => now < until,
    until: () => until,
  };
}

const listeners = new Set<() => void>();
/** The app-wide hint (api.ts marks it, useOnline folds it into `online`). */
export const offlineHint = createOfflineHint(OFFLINE_HINT_MS, () => listeners.forEach((l) => l()));

export function subscribeOfflineHint(l: () => void) {
  listeners.add(l);
  return () => {
    listeners.delete(l);
  };
}
