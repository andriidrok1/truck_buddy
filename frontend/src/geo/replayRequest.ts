// A recorded drive the tester wants replayed. The map screen owns the active route, so the tester
// hands the recording over here and the map screen picks it up: it sets the recording's route (not a
// fresh one from HERE, whose geometry and indices would differ) and feeds the fixes to playTrace().

import type { RecordedTrace } from "./recorder";

export type ReplayRequest = { trace: RecordedTrace; speedFactor: number; name: string };

let pending: ReplayRequest | null = null;
const listeners = new Set<() => void>();

export function requestReplay(req: ReplayRequest) {
  pending = req;
  listeners.forEach((l) => l());
}

/** The request, once; null when there is none. */
export function takeReplayRequest(): ReplayRequest | null {
  const r = pending;
  pending = null;
  return r;
}

export function subscribeReplay(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}
