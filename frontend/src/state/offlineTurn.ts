// A copilot turn with no signal: nothing to transcribe with, nothing to ask. The map screen's
// submitTurn calls this before STT and before /api/agent; when it answers true the turn is over.

import { NO_SIGNAL_TURN_LINE } from "../geo/offlineGuidance";
import { PRIORITY } from "../voice/queue";
import type { SayOptions } from "../hooks/useSpeechQueue";

export type OfflineTurnDeps = {
  isOnline: () => boolean;
  say: (text: string, opts: SayOptions) => void;
  setCaption: (c: { reply: string }) => void;
};

/** True when the turn was answered offline (the caller must not record, transcribe or call the agent). */
export function offlineTurn({ isOnline, say, setCaption }: OfflineTurnDeps): boolean {
  if (isOnline()) return false;
  setCaption({ reply: NO_SIGNAL_TURN_LINE });
  say(NO_SIGNAL_TURN_LINE, { priority: PRIORITY.MANEUVER, kind: "system", key: "no-signal", ttlMs: 15000 });
  return true;
}
