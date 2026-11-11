import { useState } from "react";

import type { HowToMode } from "../components/HowToSheet";
import { howToDot, markHowToSeen, shouldAutoShowHowTo, useHowTo } from "../state/howTo";

type Options = {
  /** The map is the screen on top (not under the truck profile or history). */
  focused: boolean;
  /** The mic permission card is up. */
  micCard: boolean;
  /** The copilot listens or thinks. */
  busy: boolean;
  /** A typed request (the map's onSubmitText). */
  ask: (text: string) => void;
  /** Start listening, as a tap on the avatar. */
  listen: () => void;
};

/**
 * The map's side of "How to use" (src/state/howTo.ts): opens it by itself once after onboarding, opens it from "?"
 * and the sheet's link, and turns a chip or "Try it now" into the same request a typed text or an avatar tap makes.
 * Every close marks it seen.
 */
export function useHowToFlow({ focused, micCard, busy, ask, listen }: Options) {
  const state = useHowTo();
  const [mode, setMode] = useState<HowToMode | null>(null);
  if (shouldAutoShowHowTo(state, { focused, micCard, busy, open: mode !== null })) setMode("first");

  const close = () => {
    setMode(null);
    markHowToSeen().catch(() => false);
  };
  return {
    mode,
    /** The blue dot on "?". */
    dot: howToDot(state),
    /** The height card waits: while the flag is read (or it would flash for a frame), while the screen is due to
     *  open by itself, and while it is open. */
    holdsCards: state === undefined || state === "due" || mode !== null,
    open: () => setMode("help"),
    close,
    ask: (phrase: string) => {
      close();
      ask(phrase);
    },
    tryNow: () => {
      close();
      listen();
    },
  };
}
