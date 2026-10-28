// What a copilot reply does to the map screen, as data: a state patch plus an ordered list of
// effects. app/index.tsx applies the patch and runs the effects; this file never touches React,
// refs, timers or audio, so the branching can be unit-tested against fixtures/responses/*.json.
//
// Ordering matters and mirrors the old inline code: `backToIdle` comes before action effects (an
// action may change the phase after it), action effects before `speak`, and `continueDialog` last
// so it gets the spoken promise (possibly wrapped by `parkingFollowup`).

import { MESSAGES } from "../constants";
import type { TruckProfile } from "../hooks/useTruckProfile";
import type { AgentResponse, AppAction, Approach, ParkingCheck, ParkingState, Phase, Place, RouteInfo } from "../types";

export type AgentPatch = {
  options?: Place[];
  places?: Place[];
  parking?: Record<string, ParkingState>;
  phase?: Phase;
  /** Same destination, new route ("another way?", a via stop added or cleared): swap it in place. */
  activeRoute?: { route: RouteInfo; stops: Place[] };
};

export type Effect =
  | { type: "dial"; phone: string }
  /** Breakdown research in flight: wait for it (index.tsx: afterFiller + runResearch). Always alone. */
  | { type: "research"; id: string }
  | { type: "startRoute"; place: Place; route: RouteInfo; parking?: ParkingCheck | null }
  /** Play res.reply; later effects see its promise. */
  | { type: "speak" }
  | { type: "toast"; text: string }
  | { type: "backToIdle" }
  | { type: "pauseDialog" }
  | { type: "endDialog" }
  /** `force`: reopen the mic after the reply even if the loop was paused (a confirm question needs its yes/no). */
  | { type: "continueDialog"; force?: boolean }
  /** Satellite parking check for the new destination, spoken after the reply. */
  | { type: "followParking"; place: Place; approach?: Approach | null }
  /** "Is there parking at X?": wrap the spoken promise with the parking answer for that place. */
  | { type: "parkingFollowup"; placeId: string }
  | { type: "clearRoute" }
  | { type: "startDriving" }
  | { type: "openOptions" }
  | { type: "recenter" }
  | { type: "overview" }
  | { type: "zoomBy"; delta: number }
  | { type: "setVoiceGuidance"; on: boolean }
  | { type: "setHandsFree"; on: boolean }
  /** Nudges while driving (break, chains, parking offers) on or off. */
  | { type: "setNudges"; on: boolean }
  | { type: "saveProfile"; patch: Partial<TruckProfile> }
  /** Save the patch, then re-plan the active route with it. */
  | { type: "reroute"; patch: Partial<TruckProfile> };

export type AgentContext = {
  /** Destination of the active route, null without one. */
  activePlaceId?: string | null;
  /** Alternatives currently in the options sheet. */
  optionsCount: number;
  /** Current favourite chains (add/remove_favorite_chain build on them). */
  favoriteChains?: string[];
};

export type AgentReduction = { patch: AgentPatch; effects: Effect[] };

/** Effects for the app controls the copilot returned (trip, sound, map, truck data, preferences). */
export function reduceActions(actions: AppAction[], ctx: AgentContext): Effect[] {
  const effects: Effect[] = [];
  let truckPatch: Partial<TruckProfile> = {};
  const chains = () => truckPatch.favorite_chains ?? ctx.favoriteChains ?? [];
  for (const { type, value } of actions) {
    switch (type) {
      case "start_navigation":
        if (ctx.activePlaceId) effects.push({ type: "startDriving" });
        break;
      case "end_route":
        effects.push({ type: "endDialog" }, { type: "clearRoute" });
        break;
      case "show_options":
        if (ctx.optionsCount) effects.push({ type: "openOptions" });
        break;
      case "recenter":
        effects.push({ type: "recenter" });
        break;
      case "overview":
        if (ctx.activePlaceId) effects.push({ type: "overview" });
        break;
      case "zoom_in":
        effects.push({ type: "zoomBy", delta: 1 });
        break;
      case "zoom_out":
        effects.push({ type: "zoomBy", delta: -1 });
        break;
      case "voice_guidance":
        effects.push({ type: "setVoiceGuidance", on: value === true });
        break;
      case "wake_word":
        effects.push({ type: "setHandsFree", on: value === true });
        break;
      case "stop_talking":
        effects.push({ type: "pauseDialog" });
        break;
      case "set_nudges":
        effects.push({ type: "setNudges", on: value === true });
        break;
      case "set_height_in":
        truckPatch.height_in = Number(value);
        break;
      case "set_trailer_ft":
        truckPatch.trailer_ft = Number(value);
        break;
      case "set_weight_lb":
        truckPatch.weight_lb = Number(value);
        break;
      case "set_hazmat":
        truckPatch.hazmat = value === true;
        break;
      case "set_driver_name":
        truckPatch.driver_name = String(value);
        break;
      case "avoid_tolls":
        truckPatch.avoid_tolls = value === true;
        break;
      case "avoid_ferries_tunnels":
        truckPatch.avoid_ferries_tunnels = value === true;
        break;
      case "add_favorite_chain":
        truckPatch.favorite_chains = [...chains(), String(value)];
        break;
      case "remove_favorite_chain":
        truckPatch.favorite_chains = chains().filter((c) => c.toLowerCase() !== String(value).toLowerCase());
        break;
      case "set_break_hours":
        truckPatch.break_hours = value == null ? null : Number(value);
        break;
      case "set_chains_on_board":
        truckPatch.chains_on_board = value === true;
        break;
      case "reroute":
        // Save first so the new route uses the new profile.
        effects.push({ type: "reroute", patch: truckPatch });
        truckPatch = {};
        break;
      // parking_followup is handled by reduceAgentResponse (it wraps the spoken reply); unknown types are ignored.
    }
  }
  if (Object.keys(truckPatch).length) effects.push({ type: "saveProfile", patch: truckPatch });
  return effects;
}

/** Patch + effects for one copilot reply (the response branch of submitTurn). */
export function reduceAgentResponse(res: AgentResponse, ctx: AgentContext): AgentReduction {
  const effects: Effect[] = [];
  if (res.dial_phone) effects.push({ type: "dial", phone: res.dial_phone });

  if (res.status === "researching" && res.research_id) {
    effects.push({ type: "research", id: res.research_id });
    return { patch: {}, effects };
  }

  if (res.status === "route_set" && res.place && res.route && ctx.activePlaceId && res.place.id === ctx.activePlaceId) {
    // Route update, not a new destination: the parking check, the sheet and the dialog state stay.
    // With via stops, `options` lists the stop places in order; without them it is empty.
    const stops = res.route.via?.length ? (res.options ?? []) : [];
    effects.push({ type: "speak" });
    return { patch: { activeRoute: { route: res.route, stops } }, effects };
  }

  if (res.status === "route_set" && res.place && res.route) {
    const others = res.options ?? [];
    effects.push(
      { type: "startRoute", place: res.place, route: res.route, parking: res.parking },
      { type: "speak" },
      { type: "followParking", place: res.place, approach: res.route.approach },
      { type: "pauseDialog" }, // route is set: stop listening until the driver taps the mic again (same session)
    );
    return { patch: { options: others, parking: {}, places: [res.place, ...others] }, effects };
  }

  if (res.status === "options" && res.options?.length) {
    // A list to choose from: same sheet as the "show_options" action, dialog stays open for "the second one".
    const actions = res.actions?.length ? reduceActions(res.actions, { ...ctx, optionsCount: res.options.length }) : [];
    effects.push(...actions, { type: "speak" }, { type: "continueDialog" });
    return { patch: { options: res.options, parking: {}, places: res.options, phase: "options" }, effects };
  }

  const spokenOnly = res.status === "chat" || res.status === "confirm" || res.status === "not_understood";
  if (!spokenOnly) effects.push({ type: "toast", text: res.message ?? MESSAGES.notUnderstood });
  effects.push({ type: "backToIdle" });
  if (res.actions?.length) effects.push(...reduceActions(res.actions, ctx)); // after backToIdle: actions may change the phase
  effects.push({ type: "speak" });
  const followup = res.actions?.find((a) => a.type === "parking_followup");
  if (followup) effects.push({ type: "parkingFollowup", placeId: String(followup.value) });
  // A confirm question is resolved by the next utterance ("yes" / "no"): the mic reopens whatever the loop state.
  effects.push(res.status === "confirm" ? { type: "continueDialog", force: true } : { type: "continueDialog" });
  return { patch: {}, effects };
}
