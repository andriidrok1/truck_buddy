// The map screen's active route, mirrored into a module so screens outside it (the tester's
// replay buttons) can read it. The map screen owns the state; this is a read-only window on it.

import { useSyncExternalStore } from "react";

import type { ActiveRoute } from "../types";

let current: ActiveRoute | null = null;
const listeners = new Set<() => void>();

export const getActiveRoute = () => current;

export function setActiveRoute(a: ActiveRoute | null) {
  if (a === current) return;
  current = a;
  listeners.forEach((l) => l());
}

const subscribe = (l: () => void) => {
  listeners.add(l);
  return () => {
    listeners.delete(l);
  };
};

export function useActiveRoute(): ActiveRoute | null {
  return useSyncExternalStore(subscribe, getActiveRoute, getActiveRoute);
}
