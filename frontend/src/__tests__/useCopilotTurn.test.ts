import { createCopilotTurn, type TurnDeps } from "../dialog/useCopilotTurn";
import type { AgentResponse } from "../types";

jest.useFakeTimers();

const flush = async () => {
  for (let i = 0; i < 8; i++) await Promise.resolve();
};
const harness = (deps: TurnDeps) => {
  const ctrl = createCopilotTurn(deps);
  const current = Object.assign({}, ctrl) as typeof ctrl & { state: ReturnType<typeof ctrl.getState> };
  Object.defineProperty(current, "state", { get: () => ctrl.getState() }); // live, not a copy of the initial state
  return { result: { current } };
};
const act = (fn: () => void) => fn();

type Fake = { deps: TurnDeps; calls: string[]; listen: jest.Mock; transcribe: jest.Mock; agent: jest.Mock; listenResult: () => Promise<string>; agentResult: () => Promise<AgentResponse> };

function fakeDeps(): Fake {
  const calls: string[] = [];
  const f: Partial<Fake> = {};
  // The first listen answers as configured; later ones wait like a mic waits for a driver (the hands-free loop
  // would otherwise run forever in a test).
  let listens = 0;
  f.listenResult = () => Promise.resolve("file://rec.m4a");
  const listenOnce = () => (listens++ === 0 ? f.listenResult!() : new Promise<string>(() => undefined));
  f.agentResult = () => Promise.resolve({ status: "chat", transcript: "hi", reply: "Hello.", source: "t" } as AgentResponse);
  f.listen = jest.fn(() => listenOnce());
  f.transcribe = jest.fn(async () => ({ transcript: "take me to Reno", stt_ms: 400, stt_confidence: 0.9 }));
  f.agent = jest.fn((...args: unknown[]) => {
    const signal = args[3] as AbortSignal;
    return new Promise<AgentResponse>((resolve, reject) => {
      signal.addEventListener("abort", () => reject(new Error("aborted")));
      f.agentResult!().then(resolve, reject);
    });
  });
  const plan = () => ({ picked: null, done: Promise.resolve(), cancel: () => true, startedAt: () => null, elapsed: () => 0 });
  f.deps = {
    voice: { listen: f.listen as never, cancelRecording: async () => undefined, stopSpeaking: () => calls.push("stopSpeaking") },
    api: { transcribe: f.transcribe as never, agentSpeak: f.agent as never },
    fillers: { schedule: plan, noteTurn: () => undefined },
    offline: () => false,
    context: () => ({ coords: { lat: 1, lng: 2 }, active: null, ui: { driving: false, voice_guidance: true, hands_free: false, options_count: 0 }, heading: null, speed: null, limitMph: null, hintNames: [] }),
    onPhase: (p) => calls.push(`phase:${p}`),
    onIdle: () => calls.push("idle"),
    onThinking: () => undefined,
    onHeard: (t) => calls.push(`heard:${t}`),
    onNotUnderstood: () => calls.push("notUnderstood"),
    onError: (m) => calls.push(`error:${m}`),
    onListenFailed: () => calls.push("listenFailed"),
    onResponse: async (res) => {
      calls.push(`response:${res.status}`);
      return { spoken: Promise.resolve() };
    },
    now: () => 1_700_000_000_000,
    newId: () => "dlg-1",
  };
  f.calls = calls;
  return f as Fake;
}

test("tap → listen → STT → agent → spoken → the mic reopens (hands-free loop)", async () => {
  const f = fakeDeps();
  f.deps.autoListen = true;
  const { result } = harness(f.deps);
  act(() => result.current.tap());
  expect(f.calls).toEqual(["phase:listening"]);
  await flush();
  expect(f.transcribe).toHaveBeenCalledTimes(1);
  expect(f.calls).toContain("phase:thinking");
  expect(f.calls).toContain("heard:take me to Reno");
  expect(f.agent).toHaveBeenCalledTimes(1);
  expect(f.agent.mock.calls[0][0]).toBe("take me to Reno");
  expect(f.agent.mock.calls[0][4]).toBe("dlg-1"); // the session id
  expect(f.agent.mock.calls[0][8]).toBe(400); // stt_ms carried from the STT step
  await flush();
  expect(f.calls).toContain("response:chat");
  // the reply has played: the loop is live, so the mic opens again
  expect(f.listen).toHaveBeenCalledTimes(2);
  expect(result.current.state.mode).toBe("listening");
  expect(result.current.dialogId()).toBe("dlg-1");
});

test("silence twice closes the dialog; the first silence tells the driver nothing was heard", async () => {
  const f = fakeDeps();
  f.listenResult = () => Promise.resolve("silent");
  const { result } = harness(f.deps);
  act(() => result.current.tap());
  await flush();
  expect(f.calls).toEqual(["phase:listening", "notUnderstood", "idle"]);
  expect(result.current.state).toMatchObject({ mode: "idle", live: false, misses: 0, dialogId: "dlg-1" });
  // a typed turn the copilot does not understand, twice, ends the session
  f.agentResult = () => Promise.resolve({ status: "not_understood", transcript: "x", reply: "?", source: "t" } as AgentResponse);
  act(() => result.current.text("blorp"));
  await flush();
  expect(result.current.state.misses).toBe(1);
  act(() => result.current.text("blorp again"));
  await flush();
  expect(result.current.dialogId()).toBeNull();
});

test("a confirm question reopens the mic even when the loop is paused (hands-free loop)", async () => {
  const f = fakeDeps();
  f.deps.autoListen = true;
  f.agentResult = () => Promise.resolve({ status: "confirm", transcript: "Reno", reply: "Did you say Reno?", source: "t" } as AgentResponse);
  f.listenResult = () => new Promise<string>(() => undefined); // the driver has not answered yet
  const { result } = harness(f.deps);
  act(() => result.current.text("take me to Reno")); // typed: the loop is not live...
  await flush();
  expect(f.listen).toHaveBeenCalledTimes(1); // ...yet the question opened the mic (and, as before, the loop is live from there)
  expect(result.current.state).toMatchObject({ mode: "listening", live: true, pendingConfirm: false });
});

test("cancel aborts the agent call in flight and keeps the session", async () => {
  const f = fakeDeps();
  let resolveAgent: (r: AgentResponse) => void = () => undefined;
  f.agentResult = () => new Promise<AgentResponse>((r) => (resolveAgent = r));
  const { result } = harness(f.deps);
  act(() => result.current.text("find a truck stop"));
  await flush();
  expect(f.agent).toHaveBeenCalledTimes(1);
  const signal = f.agent.mock.calls[0][3] as AbortSignal;
  act(() => result.current.cancel());
  expect(signal.aborted).toBe(true);
  expect(f.calls).toContain("stopSpeaking");
  resolveAgent({ status: "chat", transcript: "", reply: "late", source: "t" } as AgentResponse);
  await flush();
  expect(f.calls).not.toContain("response:chat"); // the late answer is dropped
  expect(f.calls.filter((c) => c.startsWith("error:"))).toEqual([]); // and not reported as a failure
  expect(result.current.dialogId()).toBe("dlg-1");
});

test("endRoute closes the dialog; a failed turn shows the message and counts a miss", async () => {
  const f = fakeDeps();
  f.agentResult = () => Promise.reject(new Error("network down"));
  const { result } = harness(f.deps);
  act(() => result.current.text("hello"));
  await flush();
  expect(f.calls.some((c) => c.startsWith("error:I can't reach the server"))).toBe(true);
  expect(f.calls).toContain("idle");
  expect(result.current.state.misses).toBe(1);
  act(() => result.current.endRoute());
  expect(result.current.dialogId()).toBeNull();
  expect(result.current.state.live).toBe(false);
});

test("offline: a tap pauses the loop and does not record", () => {
  const f = fakeDeps();
  f.deps.offline = () => true;
  const { result } = harness(f.deps);
  act(() => result.current.tap());
  expect(f.listen).not.toHaveBeenCalled();
  expect(f.calls).toEqual(["idle"]);
});

test("walkie-talkie (default): after the answer the mic stays shut", async () => {
  const f = fakeDeps();
  const { result } = harness(f.deps);
  act(() => result.current.tap());
  await flush();
  await flush();
  expect(f.calls).toContain("response:chat");
  expect(f.listen).toHaveBeenCalledTimes(1);
  expect(result.current.state).toMatchObject({ mode: "idle", dialogId: "dlg-1" });
});

test("a doubtful recording goes to STT as maybe; empty means silence, no agent call and no filler", async () => {
  const f = fakeDeps();
  const schedule = jest.fn(f.deps.fillers.schedule);
  f.deps.fillers.schedule = schedule;
  f.listenResult = () => Promise.resolve({ uri: "file://rec.m4a", maybe: true }) as never;
  f.transcribe.mockImplementation(async () => ({ transcript: "", stt_ms: 300, stt_confidence: null }));
  const { result } = harness(f.deps);
  act(() => result.current.tap());
  await flush();
  expect(f.transcribe.mock.calls[0][3]).toBe(true);
  expect(schedule).not.toHaveBeenCalled();
  expect(f.agent).not.toHaveBeenCalled();
  expect(f.calls).toEqual(["phase:listening", "phase:thinking", "notUnderstood", "idle"]);
  expect(result.current.state.mode).toBe("idle");
});

test("a doubtful recording with words is an ordinary turn", async () => {
  const f = fakeDeps();
  f.listenResult = () => Promise.resolve({ uri: "file://rec.m4a", maybe: true }) as never;
  const { result } = harness(f.deps);
  act(() => result.current.tap());
  await flush();
  await flush();
  expect(f.agent.mock.calls[0][0]).toBe("take me to Reno");
});

test("a tap while the words are still being transcribed drops that request and its filler", async () => {
  const f = fakeDeps();
  const plan = { picked: "hang-on", done: Promise.resolve(), cancel: jest.fn(() => true), startedAt: () => null, elapsed: () => 0 };
  f.deps.fillers.schedule = () => plan;
  let sttSignal: AbortSignal | undefined;
  f.transcribe.mockImplementation((_uri: string, signal?: AbortSignal) => {
    sttSignal = signal;
    return new Promise((_, reject) => signal?.addEventListener("abort", () => reject(new Error("aborted"))));
  });
  const { result } = harness(f.deps);
  act(() => result.current.tap());
  await flush();
  expect(f.transcribe).toHaveBeenCalledTimes(1);
  expect(result.current.state.mode).toBe("listening"); // the machine's view while STT runs
  expect(plan.cancel).not.toHaveBeenCalled();

  act(() => result.current.tap());
  expect(plan.cancel).toHaveBeenCalledTimes(1); // no "Still looking." over the new recording
  expect(sttSignal?.aborted).toBe(true);
  expect(f.calls).toContain("stopSpeaking"); // a "Hang on." already on air is cut
  expect(f.listen).toHaveBeenCalledTimes(2);
  expect(result.current.state.mode).toBe("listening");
  await flush();
  expect(f.agent).not.toHaveBeenCalled(); // the first turn never reaches the agent
  expect(f.calls.filter((c) => c.startsWith("error:"))).toEqual([]);
  expect(result.current.dialogId()).toBe("dlg-1"); // same session
});

test("a tap while the mic is recording does not cancel anything here (the screen finishes the recording)", () => {
  const f = fakeDeps();
  f.listenResult = () => new Promise<string>(() => undefined);
  const { result } = harness(f.deps);
  act(() => result.current.tap());
  expect(f.calls).not.toContain("stopSpeaking");
  act(() => result.current.tap());
  expect(f.calls).not.toContain("stopSpeaking"); // no "cancel" was dispatched, as before
});
