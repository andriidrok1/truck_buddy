// Runs the effects src/state/agentReducer.ts returns for one copilot answer, in order, through the
// handlers the map screen supplies. This used to be a switch inside the screen; here it is a function the
// screen hands its handlers to, and a plug point for anything else that produces effects (a dispatcher's
// push is a `startRoute` effect). Only `research` is awaited; everything else runs in this tick.

import type { Effect } from "./agentReducer";
import { setMapView } from "./settingsStore";
import type { TruckProfile } from "../hooks/useTruckProfile";
import type { AgentResponse, Approach, Place } from "../types";

export type EffectHandlers = {
  dial: (phone: string) => void;
  /** Breakdown: waits for the research; resolves with the promise of its spoken result (wrapped, so the
   *  outer await does not flatten it). */
  research: (res: AgentResponse) => Promise<{ spoken: Promise<void> }>;
  startRoute: (place: Place, route: NonNullable<AgentResponse["route"]>, parking: AgentResponse["parking"]) => void;
  /** Speaks the reply; the returned promise is what later effects chain on. */
  speak: () => Promise<void>;
  toast: (text: string) => void;
  backToIdle: () => void;
  pauseDialog: () => void;
  endDialog: () => void;
  /** The dialog decides itself whether the mic reopens; kept as a hook for the screen's own bookkeeping. */
  continueDialog: (force: boolean) => void;
  followParking: (place: Place, spoken: Promise<void>, approach: Approach | null | undefined) => void;
  /** Returns the new "spoken" promise (the follow-up line plays after the reply). */
  parkingFollowup: (placeId: string, spoken: Promise<void>) => Promise<void> | null;
  clearRoute: () => void;
  startDriving: () => void;
  openOptions: () => void;
  recenter: () => void;
  overview: () => void;
  zoomBy: (delta: number) => void;
  setVoiceGuidance: (on: boolean) => void;
  setHandsFree: (on: boolean) => void;
  setNudges: (on: boolean) => void;
  saveProfile: (patch: Partial<TruckProfile>) => void;
  reroute: (patch: Partial<TruckProfile>) => void;
  unknownAction: (action: string) => void;
};

/** Applies `effects` for `res`; resolves with the promise of the spoken reply (already resolved when nothing was spoken). */
export async function applyEffects(effects: readonly Effect[], res: AgentResponse, h: EffectHandlers): Promise<{ spoken: Promise<void> }> {
  let spoken: Promise<void> = Promise.resolve();
  for (const e of effects) {
    switch (e.type) {
      case "dial":
        h.dial(e.phone);
        break;
      case "research":
        spoken = (await h.research(res)).spoken;
        break;
      case "startRoute":
        h.startRoute(e.place, e.route, e.parking);
        break;
      case "speak":
        spoken = h.speak();
        break;
      case "toast":
        h.toast(e.text);
        break;
      case "backToIdle":
        h.backToIdle();
        break;
      case "pauseDialog":
        h.pauseDialog();
        break;
      case "endDialog":
        h.endDialog();
        break;
      case "continueDialog":
        h.continueDialog(!!e.force);
        break;
      case "followParking":
        h.followParking(e.place, spoken, e.approach);
        break;
      case "parkingFollowup": {
        const next = h.parkingFollowup(e.placeId, spoken);
        if (next) spoken = next;
        break;
      }
      case "clearRoute":
        h.clearRoute();
        break;
      case "startDriving":
        h.startDriving();
        break;
      case "openOptions":
        h.openOptions();
        break;
      case "recenter":
        h.recenter();
        break;
      case "overview":
        h.overview();
        break;
      case "zoomBy":
        h.zoomBy(e.delta);
        break;
      case "setVoiceGuidance":
        h.setVoiceGuidance(e.on);
        break;
      case "setHandsFree":
        h.setHandsFree(e.on);
        break;
      case "setNudges":
        h.setNudges(e.on);
        break;
      case "setMapView":
        setMapView(e.view); // a stored setting, like the map view sheet's pick
        break;
      case "saveProfile":
        h.saveProfile(e.patch);
        break;
      case "reroute":
        h.reroute(e.patch);
        break;
      case "unknownAction":
        h.unknownAction(e.action);
        break;
    }
  }
  return { spoken };
}
