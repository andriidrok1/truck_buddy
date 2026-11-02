// The app's one drive recorder: real fixes from the location store, files through traceFiles.ts.

import { useSyncExternalStore } from "react";

import { getLocation, subscribeLocation } from "./location";
import { createRecorder } from "./recorder";
import { writeTrace } from "./traceFiles";

export const recorder = createRecorder({ subscribe: subscribeLocation, getLocation, now: Date.now, write: writeTrace });

/** Set in the build for a day on the road: every drive is recorded from Go to End route. */
export const RECORD_DRIVES = process.env.EXPO_PUBLIC_RECORD_DRIVES === "1";

const snapshot = () => `${recorder.isRecording()}:${recorder.current()?.fixes.length ?? 0}:${recorder.lastFile() ?? ""}`;

export function useRecorder(): { recording: boolean; fixes: number; lastFile: string | null } {
  useSyncExternalStore(recorder.subscribe, snapshot, snapshot);
  return { recording: recorder.isRecording(), fixes: recorder.current()?.fixes.length ?? 0, lastFile: recorder.lastFile() };
}
