import chat from "../../fixtures/responses/chat.json";
import options from "../../fixtures/responses/options.json";
import routeSet from "../../fixtures/responses/route_set.json";
import { MESSAGES } from "../constants";
import { reduceActions, reduceAgentResponse, type Effect } from "../state/agentReducer";
import type { AgentResponse, AppAction } from "../types";

const routeSetRes = routeSet as unknown as AgentResponse;
const optionsRes = options as unknown as AgentResponse;
const chatRes = chat as unknown as AgentResponse;

const idle = { activePlaceId: null, optionsCount: 0 };
const onRoute = { activePlaceId: "ChIJpilot-sac-001", optionsCount: 2 };
const types = (effects: Effect[]) => effects.map((e) => e.type);

describe("reduceAgentResponse", () => {
  test("route_set sets options without the chosen place", () => {
    const alt = { ...optionsRes.options![0], id: "alt-1" };
    const res: AgentResponse = { ...routeSetRes, options: [alt] };
    const { patch, effects } = reduceAgentResponse(res, idle);
    expect(patch.options).toEqual([alt]);
    expect(patch.places?.map((p) => p.id)).toEqual(["ChIJpilot-sac-001", "alt-1"]);
    expect(patch.parking).toEqual({});
    expect(patch.phase).toBeUndefined(); // startRoute switches to "navigating"
    expect(types(effects)).toEqual(["startRoute", "speak", "followParking", "pauseDialog"]);
    const start = effects[0];
    if (start.type !== "startRoute") throw new Error("expected startRoute");
    expect(start.place.id).toBe("ChIJpilot-sac-001");
    expect(start.route.geometry).toHaveLength(5);
    expect(start.parking?.verdict).toBe("listed");
    const follow = effects[2];
    if (follow.type !== "followParking") throw new Error("expected followParking");
    expect(follow.approach?.compass).toBe("SE");
    // The fixture's set_driving action is not an app control and is ignored; route_set runs no actions anyway.
    expect(effects.some((e) => e.type === "startDriving")).toBe(false);
  });

  test("route_set same place updates route and stops", () => {
    const stop = { ...optionsRes.options![0], id: "stop-fernley", name: "Pilot Fernley" };
    const route = { ...routeSetRes.route!, summary: "I-80 E via Fernley", via: [[39.6, -119.25]] as [number, number][] };
    const res: AgentResponse = { ...routeSetRes, route, options: [stop] };
    const { patch, effects } = reduceAgentResponse(res, onRoute);
    expect(types(effects)).toEqual(["speak"]); // no startRoute, no followParking, no pauseDialog
    expect(patch).toEqual({ activeRoute: { route, stops: [stop] } });
    expect(patch.options).toBeUndefined(); // the options sheet is left alone
    expect(patch.parking).toBeUndefined();
  });

  test("route_set same place with empty via clears stops", () => {
    const route = { ...routeSetRes.route!, via: [] as [number, number][] };
    // Even if the backend echoed something in `options`, no via means no stops.
    const res: AgentResponse = { ...routeSetRes, route, options: [optionsRes.options![0]] };
    const { patch, effects } = reduceAgentResponse(res, onRoute);
    expect(types(effects)).toEqual(["speak"]);
    expect(patch.activeRoute).toEqual({ route, stops: [] });
    // "another way?": no via field at all is the same thing.
    const other = reduceAgentResponse({ ...routeSetRes, options: [] }, onRoute);
    expect(other.patch.activeRoute?.stops).toEqual([]);
  });

  test("route_set new place starts a route", () => {
    const place = { ...routeSetRes.place!, id: "ChIJhotel-002", name: "Motel 6" };
    const { patch, effects } = reduceAgentResponse({ ...routeSetRes, place }, onRoute);
    expect(types(effects)).toEqual(["startRoute", "speak", "followParking", "pauseDialog"]);
    expect(patch.activeRoute).toBeUndefined();
    expect(patch.places?.[0].id).toBe("ChIJhotel-002");
    // Without an active route the same id is a new destination too.
    expect(types(reduceAgentResponse(routeSetRes, idle).effects)[0]).toBe("startRoute");
  });

  test("route_set without a route falls through to the toast branch", () => {
    const { patch, effects } = reduceAgentResponse({ ...routeSetRes, route: null }, idle);
    expect(patch).toEqual({});
    expect(types(effects)).toEqual(["toast", "backToIdle", "speak", "continueDialog"]);
  });

  test("options status fills list", () => {
    const { patch, effects } = reduceAgentResponse(optionsRes, idle);
    expect(patch.options).toHaveLength(3);
    expect(patch.places).toBe(patch.options);
    expect(patch.parking).toEqual({});
    expect(patch.phase).toBe("options");
    expect(types(effects)).toEqual(["speak", "continueDialog"]); // no toast, dialog stays open
  });

  test("options with an empty list is a toast, not an empty sheet", () => {
    const { patch, effects } = reduceAgentResponse({ ...optionsRes, options: [], message: "Nothing nearby" }, idle);
    expect(patch).toEqual({});
    expect(effects[0]).toEqual({ type: "toast", text: "Nothing nearby" });
  });

  test("chat speaks and continues without a toast", () => {
    const { patch, effects } = reduceAgentResponse(chatRes, idle);
    expect(patch).toEqual({});
    expect(types(effects)).toEqual(["backToIdle", "speak", "continueDialog"]);
  });

  test("confirm reopens the mic", () => {
    // "Did you say Reno, NV?": spoken like chat, no toast, and the mic opens after the reply even
    // when the loop was paused, because the next "yes" / "no" resolves it.
    const { patch, effects } = reduceAgentResponse({ ...chatRes, status: "confirm", reply: "Did you say Reno, NV?" }, onRoute);
    expect(patch).toEqual({});
    expect(types(effects)).toEqual(["backToIdle", "speak", "continueDialog"]);
    expect(effects[2]).toEqual({ type: "continueDialog", force: true });
    // Plain chat does not force it.
    const chat = reduceAgentResponse(chatRes, onRoute);
    expect(chat.effects[2]).toEqual({ type: "continueDialog" });
  });

  test("no_results and not_understood", () => {
    const none = reduceAgentResponse({ ...chatRes, status: "no_results", message: null }, idle);
    expect(none.effects[0]).toEqual({ type: "toast", text: MESSAGES.notUnderstood });
    const huh = reduceAgentResponse({ ...chatRes, status: "not_understood" }, idle);
    expect(types(huh.effects)).toEqual(["backToIdle", "speak", "continueDialog"]);
  });

  test("researching waits for the research and nothing else", () => {
    const { patch, effects } = reduceAgentResponse({ ...chatRes, status: "researching", research_id: "r-9" }, idle);
    expect(patch).toEqual({});
    expect(effects).toEqual([{ type: "research", id: "r-9" }]);
  });

  test("dial_phone effect comes first, with any status", () => {
    const dialed = reduceAgentResponse({ ...chatRes, dial_phone: "+19165551234" }, idle);
    expect(dialed.effects[0]).toEqual({ type: "dial", phone: "+19165551234" });
    expect(types(dialed.effects)).toEqual(["dial", "backToIdle", "speak", "continueDialog"]);
    const research = reduceAgentResponse({ ...chatRes, status: "researching", research_id: "r-1", dial_phone: "911" }, idle);
    expect(types(research.effects)).toEqual(["dial", "research"]);
  });

  test("actions run after backToIdle and before speak; parking_followup wraps the reply", () => {
    const res: AgentResponse = {
      ...chatRes,
      actions: [
        { type: "parking_followup", value: "ChIJhotel-002" },
        { type: "show_options" },
      ],
    };
    const { effects } = reduceAgentResponse(res, onRoute);
    expect(types(effects)).toEqual(["backToIdle", "openOptions", "speak", "parkingFollowup", "continueDialog"]);
    expect(effects[3]).toEqual({ type: "parkingFollowup", placeId: "ChIJhotel-002" });
  });

  test("options status still runs its actions, against the new list size", () => {
    const res: AgentResponse = { ...optionsRes, actions: [{ type: "show_options" }] };
    const { effects } = reduceAgentResponse(res, idle);
    expect(types(effects)).toEqual(["openOptions", "speak", "continueDialog"]);
  });
});

describe("reduceActions", () => {
  test("reroute action saves profile first", () => {
    const actions: AppAction[] = [
      { type: "set_height_in", value: 150 },
      { type: "avoid_tolls", value: true },
      { type: "reroute" },
    ];
    const effects = reduceActions(actions, onRoute);
    expect(effects).toEqual([{ type: "reroute", patch: { height_in: 150, avoid_tolls: true } }]);
  });

  test("profile changes after a reroute are saved separately", () => {
    const effects = reduceActions([{ type: "set_trailer_ft", value: 48 }, { type: "reroute" }, { type: "set_driver_name", value: "Ray" }], onRoute);
    expect(effects).toEqual([
      { type: "reroute", patch: { trailer_ft: 48 } },
      { type: "saveProfile", patch: { driver_name: "Ray" } },
    ]);
  });

  test("profile changes without a reroute are saved once at the end", () => {
    const effects = reduceActions(
      [{ type: "set_weight_lb", value: "72000" }, { type: "set_hazmat", value: true }, { type: "set_break_hours", value: null }, { type: "recenter" }],
      idle,
    );
    expect(effects).toEqual([{ type: "recenter" }, { type: "saveProfile", patch: { weight_lb: 72000, hazmat: true, break_hours: null } }]);
  });

  test("favorite chains build on the current list, remove is case-insensitive", () => {
    const effects = reduceActions(
      [
        { type: "add_favorite_chain", value: "Love's" },
        { type: "remove_favorite_chain", value: "PILOT" },
      ],
      { ...idle, favoriteChains: ["Pilot", "TA"] },
    );
    expect(effects).toEqual([{ type: "saveProfile", patch: { favorite_chains: ["TA", "Love's"] } }]);
  });

  test("unknown action is ignored", () => {
    expect(reduceActions([{ type: "make_coffee", value: "double" }, { type: "set_driving", value: true }], onRoute)).toEqual([]);
  });

  test("trip and map controls need the matching state", () => {
    expect(reduceActions([{ type: "start_navigation" }, { type: "overview" }, { type: "show_options" }], idle)).toEqual([]);
    expect(reduceActions([{ type: "start_navigation" }, { type: "overview" }, { type: "show_options" }], onRoute)).toEqual([
      { type: "startDriving" },
      { type: "overview" },
      { type: "openOptions" },
    ]);
    expect(reduceActions([{ type: "end_route" }, { type: "zoom_in" }, { type: "zoom_out" }, { type: "stop_talking" }], onRoute)).toEqual([
      { type: "endDialog" },
      { type: "clearRoute" },
      { type: "zoomBy", delta: 1 },
      { type: "zoomBy", delta: -1 },
      { type: "pauseDialog" },
    ]);
  });

  test("toggles coerce anything but true to off", () => {
    expect(reduceActions([{ type: "voice_guidance", value: true }, { type: "wake_word", value: "yes" }], idle)).toEqual([
      { type: "setVoiceGuidance", on: true },
      { type: "setHandsFree", on: false },
    ]);
  });
});
