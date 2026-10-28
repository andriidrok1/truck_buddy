// Is the phone online? NetInfo's `isInternetReachable` (falls back to `isConnected` while the
// reachability probe is still running), folded with the offline hint from failed API calls.
// One NetInfo subscription for the app, a module getter for code outside React (the speech
// queue, the turn pipeline) and a hook for screens.

import NetInfo, { type NetInfoState } from "@react-native-community/netinfo";
import { useEffect, useSyncExternalStore } from "react";

import { offlineHint, subscribeOfflineHint } from "../net/offlineHint";

/** null until the first NetInfo event. */
let net: boolean | null = null;
let started = false;
const listeners = new Set<() => void>();
const notify = () => listeners.forEach((l) => l());
let hintTimer: ReturnType<typeof setTimeout> | null = null;

export const fromNetInfo = (s: Pick<NetInfoState, "isConnected" | "isInternetReachable">): boolean | null =>
  s.isInternetReachable ?? s.isConnected;

function start() {
  if (started) return;
  started = true;
  NetInfo.addEventListener((s) => {
    const next = fromNetInfo(s);
    if (next === net) return;
    net = next;
    notify();
  });
  // The hint flips `online` to false right away and back when its window ends.
  subscribeOfflineHint(() => {
    if (hintTimer) clearTimeout(hintTimer);
    hintTimer = null;
    const left = offlineHint.until() - Date.now();
    if (left > 0) hintTimer = setTimeout(() => notify(), left + 1);
    notify();
  });
}

/** Combined state: NetInfo's verdict, or false while a failed API call says the link is dead. */
export function getOnline(now = Date.now()): boolean | null {
  if (offlineHint.active(now)) return false;
  return net;
}

/** For code outside React: unknown (no event yet) counts as online, so nothing is blocked at startup. */
export const isOnline = () => getOnline() !== false;

const subscribe = (l: () => void) => {
  listeners.add(l);
  return () => {
    listeners.delete(l);
  };
};

export function useOnline(): { online: boolean | null } {
  useEffect(start, []);
  const online = useSyncExternalStore(subscribe, getOnline, getOnline);
  return { online };
}

/** Tests only. */
export function __resetOnline() {
  net = null;
  started = false;
  offlineHint.clear();
}
