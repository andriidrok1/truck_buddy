// The short acknowledgement ("Hang on.") the driver hears while the answer is on its way: the clips are
// synthesised once at mount and rotated per turn (src/voice/fillers.ts plans one turn). Lifted out of the
// map screen with the turn pipeline (plan A1.2).

import { useCallback, useEffect, useRef } from "react";

import { absoluteUrl, api } from "../api";
import { t, useLang } from "../i18n";
import { planFiller, type FillerPlan } from "../voice/fillers";

export const FILLER_DELAY_MS = 300; // after the driver stops talking; an answer faster than this needs no filler
export const FILLER_SECOND_MS = 4000; // first clip played and still no answer: "Still looking."
export const FILLER_SECOND = "Still looking.";
export const SLOW_TURN_MS = 2500; // a previous turn slower than this: the filler is skipped less often
// Shortest first: planFiller() picks from the top more often.
export const FILLERS = [
  "Hang on.",
  "On it.",
  "Checking.",
  "Okay, one sec.",
  "Got it, checking.",
  "Sure, one moment.",
  "Alright, let me look.",
  "Let me see what's around.",
];

export type Fillers = {
  /** A plan for the turn that just stopped recording; null when no clip is cached yet. */
  schedule: () => FillerPlan;
  /** The last turn's server time: a slow one makes the next filler more likely. */
  noteTurn: (totalMs: number) => void;
};

export function useFillers(playAndWait: (url: string, maxMs: number) => Promise<void>): Fillers {
  // Keyed by text so the dev log can name the clip; the URL is what plays.
  const urls = useRef<Map<string, string>>(new Map());
  const lastFiller = useRef<string | null>(null);
  const lastTurnSlow = useRef(false);
  // Keyed by the English text, voiced in the driver's language: "Hang on." was said in English to a Russian
  // driver on every turn (QA 2026-11-09, S1). A language change makes the set again.
  const lang = useLang();
  useEffect(() => {
    const texts = [...FILLERS, FILLER_SECOND];
    let live = true;
    Promise.allSettled(texts.map((en) => api.speak(t(en, undefined, lang)).then((r) => absoluteUrl(r.audio_url)))).then((all) => {
      if (!live) return;
      const m = new Map<string, string>();
      all.forEach((r, i) => {
        if (r.status === "fulfilled") m.set(texts[i], r.value);
      });
      urls.current = m;
    });
    return () => {
      live = false;
    };
  }, [lang]);
  const schedule = useCallback(() => {
    const have = urls.current;
    return planFiller({
      clips: FILLERS.filter((t) => have.has(t)),
      secondClip: have.has(FILLER_SECOND) ? FILLER_SECOND : null,
      delayMs: FILLER_DELAY_MS,
      secondMs: FILLER_SECOND_MS,
      skipRate: lastTurnSlow.current ? 0.1 : 0.25,
      lastClip: lastFiller.current,
      play: (clip) => {
        const url = have.get(clip);
        if (!url) return Promise.resolve();
        if (clip !== FILLER_SECOND) lastFiller.current = clip;
        return playAndWait(url, 4000);
      },
    });
  }, [playAndWait]);
  const noteTurn = useCallback((totalMs: number) => {
    lastTurnSlow.current = totalMs > SLOW_TURN_MS;
  }, []);
  return { schedule, noteTurn };
}
