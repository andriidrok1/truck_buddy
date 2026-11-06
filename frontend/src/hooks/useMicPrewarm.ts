import { useEffect } from "react";

import type { MicPermission } from "./useVoice";

/** The mic side of the map screen while it is in front (moved verbatim from app/index.tsx). */
export function useMicPrewarm(
  setPrewarm: (on: boolean) => void,
  micPermission: () => Promise<MicPermission>,
  focused: boolean,
  micCard: Exclude<MicPermission, "granted"> | null,
  appActive: boolean,
  setMicCard: (card: null) => void,
) {
  // Keep the recorder armed while this screen is in front and the mic is allowed, so a tap records at once
  // (useVoice.prepareIdle). It was wired as setPrewarm(micOk && focused && !wakeArmed) before the turn-machine
  // move (112c7e1) and got lost there: every tap then switched the session and prepared from scratch. The
  // permission check also tells useVoice it is granted, so the first tap skips that round trip too.
  useEffect(() => {
    if (!focused) {
      setPrewarm(false);
      return;
    }
    let live = true;
    micPermission()
      .then((p) => live && setPrewarm(p === "granted"))
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [focused, micCard, setPrewarm, micPermission]); // micCard: re-check once the driver allowed the mic

  // Back from Settings (or from a system dialog that answered after the card gave up): the mic is allowed now,
  // so the card has nothing left to ask.
  useEffect(() => {
    if (!micCard || !appActive || !focused) return;
    let live = true;
    micPermission()
      .then((p) => live && p === "granted" && setMicCard(null))
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [micCard, appActive, focused, micPermission, setMicCard]); // setMicCard: a state setter, stable
}
