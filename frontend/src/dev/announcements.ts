// Dev-only log of what the copilot said on its own (maneuvers, reroutes, advisories), shown on the
// tester screen so a replay can be checked without listening to the audio. Ring buffer of the
// last 50 lines; every call is a no-op in a release build.

import { useSyncExternalStore } from "react";

export type AnnouncementKind = "maneuver" | "reroute" | "advisory" | "speed" | "system" | "skipped";

export type Announcement = { t: number; text: string; kind: AnnouncementKind };

const MAX = 50;
let buffer: Announcement[] = [];
const listeners = new Set<() => void>();

export function logAnnouncement(text: string, kind: AnnouncementKind, t = Date.now()) {
  if (!__DEV__) return;
  buffer = [...buffer.slice(-(MAX - 1)), { t, text, kind }];
  listeners.forEach((l) => l());
}

export function clearAnnouncements() {
  buffer = [];
  listeners.forEach((l) => l());
}

/** Newest last. A new array on every change, so React re-renders by identity. */
export const getAnnouncements = () => buffer;

const subscribe = (l: () => void) => {
  listeners.add(l);
  return () => {
    listeners.delete(l);
  };
};

export function useAnnouncements(): Announcement[] {
  return useSyncExternalStore(subscribe, getAnnouncements, getAnnouncements);
}
