// Recorded drives on disk: documentDirectory/traces/<file>.json. Thin adapter over expo-file-system's
// File/Directory API and expo-sharing; everything that can be unit-tested lives in recorder.ts.

import { Directory, File, Paths } from "expo-file-system";
import * as Sharing from "expo-sharing";

import type { RecordedTrace } from "./recorder";

export const TRACES_DIR = "traces";

const dir = () => new Directory(Paths.document, TRACES_DIR);

export async function writeTrace(name: string, trace: RecordedTrace): Promise<void> {
  try {
    const d = dir();
    if (!d.exists) d.create({ intermediates: true, idempotent: true });
    const file = new File(d, name);
    if (!file.exists) file.create({ overwrite: true });
    file.write(JSON.stringify(trace));
  } catch (e) {
    console.log(`[trace] write ${name} failed: ${String(e)}`);
  }
}

/** Recorded drives, newest first (file names start with the date). */
export function listTraces(): string[] {
  try {
    const d = dir();
    if (!d.exists) return [];
    return d
      .list()
      .filter((f): f is File => f instanceof File && f.name.endsWith(".json"))
      .map((f) => f.name)
      .sort()
      .reverse();
  } catch (e) {
    console.log(`[trace] list failed: ${String(e)}`);
    return [];
  }
}

export async function readTrace(name: string): Promise<RecordedTrace | null> {
  try {
    const file = new File(dir(), name);
    if (!file.exists) return null;
    const parsed = JSON.parse(await file.text()) as RecordedTrace;
    return parsed && parsed.version === 1 && Array.isArray(parsed.fixes) ? parsed : null;
  } catch (e) {
    console.log(`[trace] read ${name} failed: ${String(e)}`);
    return null;
  }
}

/** The system share sheet for one recording (AirDrop, Drive, mail...). */
export async function shareTrace(name: string): Promise<boolean> {
  try {
    if (!(await Sharing.isAvailableAsync())) return false;
    await Sharing.shareAsync(new File(dir(), name).uri, { mimeType: "application/json", dialogTitle: name });
    return true;
  } catch (e) {
    console.log(`[trace] share ${name} failed: ${String(e)}`);
    return false;
  }
}
