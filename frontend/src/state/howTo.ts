// The "How to use" screen (Figma "How to use", 68:2): opens by itself once, right after onboarding, and the "?" in
// the top bar keeps a blue dot until the driver has seen it.
//
// One key, three states:
//   "due"  — onboarding finished in this version and the screen has not been shown yet: the map opens it once;
//   "seen" — it was closed once (any way: Try it now, Not now, ×, a chip, Back): no auto-show, no dot;
//   null   — nothing stored. A driver who finished onboarding before this version lands here: the screen does not
//            open by itself for them (they already know the app), but the dot on "?" is lit until they look.
// Onboarding writes "due" (markHowToDue); a fresh install has no key until then, and the map is not shown before
// onboarding anyway. The key is the phone's, like the intro flag: a sign-out keeps it, so a driver who signs in
// again (an access code is a new sub each time) is not shown the screen a second time. "Delete my data" removes the
// key, so the onboarding after it makes it "due" again.

import { useEffect, useState } from "react";

import { storage } from "../utils/storage";

export const HOW_TO_KEY = "how_to_seen_v1";

export type HowToState = "due" | "seen" | null;

let state: HowToState | undefined; // undefined = not read yet
let reading: Promise<HowToState> | null = null;
const listeners = new Set<(v: HowToState) => void>();
const emit = () => listeners.forEach((l) => l(state ?? null));

const parse = (v: unknown): HowToState => (v === "due" || v === "seen" ? v : null);

export function loadHowTo(): Promise<HowToState> {
  if (state !== undefined) return Promise.resolve(state);
  if (!reading) {
    reading = storage
      .getItem<string | null>(HOW_TO_KEY, null)
      .then((v) => {
        if (state === undefined) state = parse(v); // a write during the read wins
        emit();
        return state as HowToState;
      })
      .catch(() => {
        if (state === undefined) state = null;
        return state as HowToState;
      });
  }
  return reading;
}

/** Onboarding just finished: the map opens the screen once. A driver who has seen it (onboarding run again from
 *  the truck screen) is not shown it again. */
export async function markHowToDue(): Promise<void> {
  const now = await loadHowTo();
  if (now === "seen") return;
  state = "due";
  emit();
  await storage.setItem(HOW_TO_KEY, "due");
}

/** Any close of the screen. Synchronous in memory, so the map never opens it a second time while the write runs. */
export function markHowToSeen(): Promise<boolean> {
  if (state === "seen") return Promise.resolve(true);
  state = "seen";
  emit();
  return storage.setItem(HOW_TO_KEY, "seen");
}

/** "Delete my data": back to how a fresh install looks. */
export async function forgetHowTo(): Promise<void> {
  await storage.removeItem(HOW_TO_KEY).catch(() => false);
  state = null;
  reading = null;
  emit();
}

/** undefined while the key is being read, then the state. */
export function useHowTo(): HowToState | undefined {
  const [v, setV] = useState<HowToState | undefined>(state);
  useEffect(() => {
    listeners.add(setV);
    loadHowTo().then(setV);
    return () => {
      listeners.delete(setV);
    };
  }, []);
  return v;
}

/** The blue dot on "?": lit until the screen has been seen (not while the key is still being read). */
export const howToDot = (s: HowToState | undefined): boolean => s === null || s === "due";

/**
 * Whether the map opens the screen by itself now. The same "one thing at a time" rule as the height card
 * (app/index.tsx): not under another screen, not over the mic card, not while the copilot listens or thinks.
 * The height card in turn waits while this is due or open, so after onboarding the order is How-to, then height.
 */
export function shouldAutoShowHowTo(s: HowToState | undefined, o: { focused: boolean; micCard: boolean; busy: boolean; open: boolean }): boolean {
  return s === "due" && o.focused && !o.micCard && !o.busy && !o.open;
}

/** Tests only. */
export function resetHowToForTests() {
  state = undefined;
  reading = null;
  listeners.clear();
}
