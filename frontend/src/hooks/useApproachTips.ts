import { useEffect, useRef } from "react";

import type { ActiveRoute } from "../types";
import { APPROACH_TIPS_M, tipsLine } from "../voice/parkingLines";
import { PRIORITY } from "../voice/queue";
import type { SayOptions } from "./useSpeechQueue";

/** Moved verbatim from app/index.tsx. */
export function useApproachTips(driving: boolean, active: ActiveRoute | null, remainingM: number, say: (text: string, opts: SayOptions) => void) {
  // Half a mile out, once per destination: the entry and parking tips from the card, through the speech
  // queue (a due turn still comes first).
  const tipsSaidFor = useRef<string | null>(null);
  useEffect(() => {
    if (!driving || !active?.parking || remainingM == null || remainingM > APPROACH_TIPS_M) return;
    if (tipsSaidFor.current === active.place.id) return;
    const text = tipsLine(active.parking);
    if (!text) return;
    tipsSaidFor.current = active.place.id;
    say(text, { priority: PRIORITY.CLOSURE_CHAIN, kind: "system", key: "approach-tips", ttlMs: 5 * 60 * 1000 });
  }, [driving, active, remainingM, say]);
}
