// Is the phone online? One place for NetInfo's verdict (`isInternetReachable`, falling back to `isConnected`
// while the reachability probe runs) folded with the offline hint from failed API calls (a dead zone has
// bars but no packets). A module getter for code outside React (speech queue, turn pipeline, trip log), a
// selector hook for screens. Replaces hooks/useOnline.ts + the listener half of net/offlineHint.ts; the
// OfflineHint factory itself stays in net/offlineHint.ts.

import NetInfo, { type NetInfoState } from "@react-native-community/netinfo";
import { useEffect } from "react";
import { create } from "zustand";

import { offlineHint, subscribeOfflineHint } from "../net/offlineHint";

type NetState = {
  /** NetInfo's verdict; null until the first event. */
  net: boolean | null;
  /** True while a failed API call says the link is dead (the hint window). */
  hinted: boolean;
};

export const useNetStore = create<NetState>()(() => ({ net: null, hinted: false }));

export const fromNetInfo = (s: Pick<NetInfoState, "isConnected" | "isInternetReachable">): boolean | null =>
  s.isInternetReachable ?? s.isConnected;

let started = false;
let hintTimer: ReturnType<typeof setTimeout> | null = null;

/** Starts the NetInfo subscription and follows the hint; idempotent. */
export function startNet() {
  if (started) return;
  started = true;
  NetInfo.addEventListener((s) => {
    const next = fromNetInfo(s);
    if (next !== useNetStore.getState().net) useNetStore.setState({ net: next });
  });
  const follow = () => {
    if (hintTimer) clearTimeout(hintTimer);
    hintTimer = null;
    const left = offlineHint.until() - Date.now();
    useNetStore.setState({ hinted: left > 0 });
    if (left > 0) hintTimer = setTimeout(follow, left + 1);
  };
  subscribeOfflineHint(follow);
}

/** Combined state: NetInfo's verdict, or false while a failed API call says the link is dead. */
export function getOnline(now = Date.now()): boolean | null {
  if (offlineHint.active(now)) return false;
  return useNetStore.getState().net;
}

/** For code outside React: unknown (no event yet) counts as online, so nothing is blocked at startup. */
export const isOnline = () => getOnline() !== false;

export const selectOnline = (s: NetState): boolean | null => (s.hinted ? false : s.net);

export function useOnline(): { online: boolean | null } {
  useEffect(startNet, []);
  const online = useNetStore(selectOnline);
  return { online };
}

/** Tests only. */
export function __resetOnline() {
  started = false;
  if (hintTimer) clearTimeout(hintTimer);
  hintTimer = null;
  offlineHint.clear();
  useNetStore.setState({ net: null, hinted: false });
}
