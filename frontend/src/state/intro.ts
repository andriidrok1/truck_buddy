// The three intro screens after the language choice (src/components/intro/IntroGate.tsx): shown once, before consent.
//
// One key: "seen" once the driver went through the screens or skipped them. Nothing stored means a fresh install,
// with one exception: a driver who passed first launch before the intro existed (consent already given, or
// onboarding finished) has no key either, and must not be sent back to "what this app is". The first read settles
// that and writes the key, so a later consent version (the consent screen comes back) does not bring the intro too.
// "Delete my data" removes the key: the intro comes back with the language screen.

import { useEffect, useState } from "react";

import { CONSENT_KEY } from "../consent";
import { isOnboarded } from "../hooks/useTruckProfile";
import { storage } from "../utils/storage";

export const INTRO_KEY = "intro_seen_v1";

let seen: boolean | undefined; // undefined = not read yet
let reading: Promise<boolean> | null = null;
const listeners = new Set<(v: boolean) => void>();
const emit = () => listeners.forEach((l) => l(seen ?? false));

/** An install from before the intro: it agreed to the consent screen or finished onboarding already. */
async function passedFirstLaunch(): Promise<boolean> {
  if ((await storage.getItem(CONSENT_KEY, false as boolean)) === true) return true;
  return isOnboarded();
}

export function loadIntro(): Promise<boolean> {
  if (seen !== undefined) return Promise.resolve(seen);
  if (!reading) {
    reading = (async () => {
      let stored = false;
      try {
        stored = (await storage.getItem<string | null>(INTRO_KEY, null)) === "seen";
        if (!stored && (await passedFirstLaunch())) {
          stored = true;
          if (seen === undefined) storage.setItem(INTRO_KEY, "seen").catch(() => {});
        }
      } catch {
        stored = false;
      }
      if (seen === undefined) seen = stored; // a write during the read wins
      emit();
      return seen;
    })();
  }
  return reading;
}

/** The last screen's button or "Skip". Synchronous in memory, so the root moves on while the write runs. */
export function markIntroSeen(): Promise<boolean> {
  if (seen === true) return Promise.resolve(true);
  seen = true;
  emit();
  return storage.setItem(INTRO_KEY, "seen");
}

/** "Delete my data": back to how a fresh install looks. */
export async function forgetIntro(): Promise<void> {
  await storage.removeItem(INTRO_KEY).catch(() => false);
  seen = false;
  reading = null;
  emit();
}

/** undefined while the key is being read, then whether the intro is behind the driver. */
export function useIntroSeen(): boolean | undefined {
  const [v, setV] = useState<boolean | undefined>(seen);
  useEffect(() => {
    listeners.add(setV);
    loadIntro().then(setV);
    return () => {
      listeners.delete(setV);
    };
  }, []);
  return v;
}

/** Tests only. */
export function resetIntroForTests() {
  seen = undefined;
  reading = null;
  listeners.clear();
}
