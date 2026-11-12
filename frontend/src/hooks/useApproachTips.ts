import { useEffect, useRef } from "react";

import type { ActiveRoute } from "../types";
import { APPROACH_TIPS_M, tipsLine } from "../voice/parkingLines";
import { PRIORITY } from "../voice/queue";
import type { SayOptions } from "./useSpeechQueue";

/** Moved verbatim from app/index.tsx. */
export function useApproachTips(driving: boolean, active: ActiveRoute | null, remainingM: number, say: (text: string, opts: SayOptions) => void) {
  // Half a mile out, once per trip: the entry and parking tips from the card, through the speech
  // queue (a due turn still comes first). Keyed on the trip, not the place: with the place id a second
  // trip to the same truck stop never got them, for as long as the app lived.
  const tipsSaidFor = useRef<string | null>(null);
  useEffect(() => {
    if (!driving || !active?.parking || remainingM == null || remainingM > APPROACH_TIPS_M) return;
    const tripKey = active.trip_id ?? active.place.id;
    if (tipsSaidFor.current === tripKey) return;
    const text = tipsLine(active.parking);
    if (!text) return;
    tipsSaidFor.current = tripKey;
    say(text, { priority: PRIORITY.CLOSURE_CHAIN, kind: "system", key: "approach-tips", ttlMs: 5 * 60 * 1000 });
  }, [driving, active, remainingM, say]);
}
