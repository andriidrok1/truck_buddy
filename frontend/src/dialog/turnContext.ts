import { WAKE_SUPPORTED } from "../hooks/useWakeWord";
import type { useTripStore } from "../state/tripStore";
import type { LatLng } from "../types";
import { streetFromInstruction } from "../voice/sttHint";
import type { TurnContext } from "./useCopilotTurn";

/** What the screen knows at the moment a turn needs it (read from its refs by the caller). */
export type TurnNow = {
  coords: LatLng;
  voiceOn: boolean;
  handsFree: boolean;
  heading: number | null | undefined;
  speed: number | null | undefined;
  limitMph: number | null;
  nextInstruction: string | undefined;
};

/** The context a copilot turn sends with the request (moved verbatim from app/index.tsx). */
export function turnContext(t: ReturnType<typeof useTripStore.getState>, now: TurnNow): TurnContext {
  const cur = t.active;
  return {
    coords: now.coords,
    active: cur,
    ui: { driving: t.driving, voice_guidance: now.voiceOn, hands_free: now.handsFree && WAKE_SUPPORTED, options_count: t.options.length,
      option_ids: t.options.slice(0, 20).map((p) => p.id) },
    heading: now.heading ?? null,
    speed: now.speed ?? null,
    limitMph: now.limitMph,
    // Names the recogniser should expect: what is on screen and on the route, most useful first.
    hintNames: [
      cur?.place.name,
      ...t.options.map((p) => p.name),
      ...t.places().map((p) => p.name),
      ...(cur?.stops ?? []).map((s) => s.name),
      ...(cur?.route.labels ?? []),
      streetFromInstruction(now.nextInstruction),
    ],
  };
}
