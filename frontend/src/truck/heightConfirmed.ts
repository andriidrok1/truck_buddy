// Has the driver told us the truck's height? Routing under bridges goes by it, and the default (13'6") is silent:
// a driver who skipped onboarding with a 14 ft rig was routed under 13'8" bridges as if they fit (vault note
// "TruckBuddy — Низкие мосты", 2026-11-04). The map asks until it is answered, and Go waits for it.
//
// Confirmed by any deliberate answer: the truck step of onboarding, a save on the truck screen, the voice
// ("my truck is 14 feet"), a fleet's truck profile, or a chip on the map's card. Skip is not an answer.

import { useEffect, useState } from "react";

import { storage } from "../utils/storage";

export const HEIGHT_CONFIRMED_KEY = "height_confirmed_v1";

let confirmed: boolean | null = null; // null = not read yet
let reading: Promise<boolean> | null = null;
const listeners = new Set<(v: boolean) => void>();

export function loadHeightConfirmed(): Promise<boolean> {
  if (confirmed !== null) return Promise.resolve(confirmed);
  if (!reading) {
    reading = storage
      .getItem<boolean>(HEIGHT_CONFIRMED_KEY, false)
      .then((v) => {
        if (confirmed === null) confirmed = v === true; // a confirm during the read wins
        listeners.forEach((l) => l(confirmed as boolean));
        return confirmed as boolean;
      })
      .catch(() => {
        if (confirmed === null) confirmed = false;
        return confirmed as boolean;
      });
  }
  return reading;
}

export async function confirmHeight(): Promise<void> {
  if (confirmed === true) return;
  confirmed = true;
  listeners.forEach((l) => l(true));
  await storage.setItem(HEIGHT_CONFIRMED_KEY, true).catch(() => {});
}

/** "Delete my data": the height is unknown again, the map asks for it. */
export async function forgetHeight(): Promise<void> {
  await storage.removeItem(HEIGHT_CONFIRMED_KEY).catch(() => false);
  confirmed = false;
  reading = null;
  listeners.forEach((l) => l(false));
}

export const isHeightConfirmed = () => confirmed === true;

/** Whether the map may ask for the height at all: the height is known to be unconfirmed, and no returning
 *  driver's truck is still on its way (`restoring` from src/data/profile.ts useRestorePending). Asking then would
 *  save the phone's 13'6" default as the driver's answer before the real truck lands. */
export const heightCardAllowed = (confirmed: boolean | null, restoring: boolean | undefined): boolean => confirmed === false && restoring === false;

/** null while the flag is being read (show nothing yet), then true / false. */
export function useHeightConfirmed(): boolean | null {
  const [v, setV] = useState<boolean | null>(confirmed);
  useEffect(() => {
    listeners.add(setV);
    loadHeightConfirmed().then(setV);
    return () => {
      listeners.delete(setV);
    };
  }, []);
  return v;
}

/** Tests only. */
export function resetHeightConfirmedForTests() {
  confirmed = null;
  reading = null;
  listeners.clear();
}
