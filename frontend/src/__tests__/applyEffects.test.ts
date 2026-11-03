import chat from "../../fixtures/responses/chat.json";
import options from "../../fixtures/responses/options.json";
import routeSet from "../../fixtures/responses/route_set.json";
import { reduceAgentResponse } from "../state/agentReducer";
import { applyEffects, type EffectHandlers } from "../state/applyEffects";
import type { AgentResponse } from "../types";

const ctx = { activePlaceId: null, optionsCount: 0, favoriteChains: [] as string[] };

function fakeHandlers(log: string[]): EffectHandlers {
  const h = {} as EffectHandlers;
  const names: (keyof EffectHandlers)[] = [
    "dial", "research", "startRoute", "speak", "toast", "backToIdle", "pauseDialog", "endDialog", "continueDialog", "followParking",
    "parkingFollowup", "clearRoute", "startDriving", "openOptions", "recenter", "overview", "zoomBy", "setVoiceGuidance", "setHandsFree",
    "setNudges", "saveProfile", "reroute", "unknownAction",
  ];
  for (const n of names) {
    (h as unknown as Record<string, unknown>)[n] = jest.fn((...args: unknown[]) => {
      log.push(n);
      if (n === "speak") return Promise.resolve();
      if (n === "research") return Promise.resolve({ spoken: Promise.resolve() });
      if (n === "parkingFollowup") return (args[1] as Promise<void>).then(() => log.push("followup-played"));
      return undefined;
    });
  }
  return h;
}

test("a route answer: start the route, speak, check parking, pause the loop, in that order", async () => {
  const log: string[] = [];
  const h = fakeHandlers(log);
  const res = routeSet as unknown as AgentResponse;
  const { effects } = reduceAgentResponse(res, ctx);
  const { spoken } = await applyEffects(effects, res, h);
  await spoken;
  expect(log.filter((n) => n !== "unknownAction")).toEqual(["startRoute", "speak", "followParking", "pauseDialog"]);
  expect(h.startRoute).toHaveBeenCalledWith(res.place, res.route, res.parking);
  // followParking chains on the spoken promise
  expect((h.followParking as jest.Mock).mock.calls[0][1]).toBeInstanceOf(Promise);
});

test("options: the actions, then speak, then the dialog goes on", async () => {
  const log: string[] = [];
  const h = fakeHandlers(log);
  const res = options as unknown as AgentResponse;
  const { effects } = reduceAgentResponse(res, ctx);
  await applyEffects(effects, res, h);
  expect(log.at(-2)).toBe("speak");
  expect(log.at(-1)).toBe("continueDialog");
});

test("chat: back to idle, speak, continue; a confirm question forces the mic", async () => {
  const log: string[] = [];
  const h = fakeHandlers(log);
  const res = chat as unknown as AgentResponse;
  await applyEffects(reduceAgentResponse(res, ctx).effects, res, h);
  expect(log).toEqual(["backToIdle", "speak", "continueDialog"]);
  expect(h.continueDialog).toHaveBeenCalledWith(false);
  const confirm = { ...res, status: "confirm", reply: "Did you say Reno?" } as AgentResponse;
  const log2: string[] = [];
  const h2 = fakeHandlers(log2);
  await applyEffects(reduceAgentResponse(confirm, ctx).effects, confirm, h2);
  expect(h2.continueDialog).toHaveBeenCalledWith(true);
});

test("an error answer shows a toast and still speaks; a parking follow-up replaces the spoken promise", async () => {
  const log: string[] = [];
  const h = fakeHandlers(log);
  const res = { ...(chat as unknown as AgentResponse), status: "error", message: "Routing is down" } as AgentResponse;
  await applyEffects(reduceAgentResponse(res, ctx).effects, res, h);
  expect(h.toast).toHaveBeenCalledWith("Routing is down");
  expect(log).toEqual(["toast", "backToIdle", "speak", "continueDialog"]);
  const withFollowup = { ...(chat as unknown as AgentResponse), actions: [{ type: "parking_followup", value: "p1" }] } as AgentResponse;
  const log2: string[] = [];
  const h2 = fakeHandlers(log2);
  const { spoken } = await applyEffects(reduceAgentResponse(withFollowup, ctx).effects, withFollowup, h2);
  await spoken;
  expect(log2).toContain("parkingFollowup");
  expect(log2.at(-1)).toBe("followup-played"); // the follow-up's own promise is what callers wait on
});

test("research is awaited and its spoken result is what comes back", async () => {
  const log: string[] = [];
  const h = fakeHandlers(log);
  const res = { ...(chat as unknown as AgentResponse), status: "researching", research_id: "r1" } as AgentResponse;
  const { spoken } = await applyEffects(reduceAgentResponse(res, ctx).effects, res, h);
  expect(log).toEqual(["research"]);
  await expect(spoken).resolves.toBeUndefined();
});
