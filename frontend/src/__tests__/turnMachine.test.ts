import { ERROR_REOPEN_MS, initialTurnState, isStale, NUDGE_ANSWER_MS, reduce, SESSION_IDLE_MS, type TurnEvent, type TurnState } from "../dialog/turnMachine";

const T0 = 1_700_000_000_000;
let n = 0;
const newId = () => `dlg-${++n}`;
const run = (s: TurnState, events: (TurnEvent | [TurnEvent, number])[], now = T0) => {
  let state = s;
  const all: ReturnType<typeof reduce>["commands"] = [];
  for (const ev of events) {
    const [e, at] = Array.isArray(ev) ? ev : [ev, now];
    const r = reduce(state, e, at, newId);
    state = r.state;
    all.push(...r.commands);
  }
  return { state, commands: all };
};
// The hands-free loop (autoListen): kept for duplex voice and the wake word; the walkie-talkie default is below.
const HF: TurnState = { ...initialTurnState, autoListen: true };
beforeEach(() => {
  n = 0;
});

test("a tap opens a session and listens; a transcript becomes a turn; the answer reopens the mic (hands-free)", () => {
  let r = run(HF, [{ type: "tap" }]);
  expect(r.commands).toEqual([{ type: "newDialog", dialogId: "dlg-1" }, { type: "startListening", turnId: 1, chime: true, maxMs: undefined }]);
  expect(r.state).toMatchObject({ mode: "listening", dialogId: "dlg-1", live: true, misses: 0, lastTurnAt: T0 });
  r = run(r.state, [{ type: "sttDone", transcript: "take me to Reno" }]);
  expect(r.commands).toEqual([{ type: "callAgent", turnId: 1, text: "take me to Reno", dialogId: "dlg-1" }]);
  expect(r.state.mode).toBe("thinking");
  r = run(r.state, [{ type: "agentDone", status: "route_set" }, { type: "speakDone" }]);
  expect(r.state.mode).toBe("listening");
  expect(r.commands.at(-1)).toMatchObject({ type: "startListening", turnId: 2 });
});

test("a route stops the loop but keeps the session; the next answer does not reopen the mic", () => {
  let r = run(HF, [{ type: "tap" }, { type: "sttDone", transcript: "x" }, { type: "routeSet" }, { type: "agentDone", status: "route_set" }, { type: "speakDone" }]);
  expect(r.state).toMatchObject({ mode: "idle", live: false, dialogId: "dlg-1" });
  expect(r.commands.filter((c) => c.type === "startListening")).toHaveLength(1);
  // the same session serves a typed follow-up
  r = run(r.state, [{ type: "text", text: "the second one" }]);
  expect(r.commands).toEqual([{ type: "callAgent", turnId: 2, text: "the second one", dialogId: "dlg-1", typed: true }]);
});

test("a confirm question forces one more listen even when the loop is paused", () => {
  const r = run(HF, [{ type: "text", text: "take me to Payson" }, { type: "agentDone", status: "confirm" }, { type: "speakDone" }]);
  expect(r.state.mode).toBe("listening");
  expect(r.state.pendingConfirm).toBe(false); // consumed by the listen
  expect(r.commands.at(-1)).toMatchObject({ type: "startListening", chime: true });
});

test("two misses in a row end the dialog; one understood answer resets the count", () => {
  let r = run(HF, [{ type: "tap" }, { type: "sttDone", transcript: "blorp" }, { type: "agentDone", status: "not_understood" }]);
  expect(r.state.misses).toBe(1);
  r = run(r.state, [{ type: "speakDone" }, { type: "sttDone", transcript: "ok" }, { type: "agentDone", status: "chat" }]);
  expect(r.state.misses).toBe(0);
  r = run(r.state, [{ type: "speakDone" }, { type: "sttDone", transcript: "a" }, { type: "agentDone", status: "not_understood" }, { type: "speakDone" }, { type: "sttDone", transcript: "b" }, { type: "agentDone", status: "not_understood" }]);
  expect(r.state).toMatchObject({ dialogId: null, live: false, misses: 0 });
  expect(r.commands.filter((c) => c.type === "closeDialog")).toHaveLength(1);
  // with no dialog the speech end goes to idle, no mic
  r = run(r.state, [{ type: "speakDone" }]);
  expect(r.state.mode).toBe("idle");
});

test("bye ends the dialog after the answer is spoken", () => {
  const r = run(HF, [{ type: "tap" }, { type: "sttDone", transcript: "thanks" }, { type: "agentDone", status: "chat", endConversation: true }, { type: "speakDone" }]);
  expect(r.state).toMatchObject({ mode: "idle", dialogId: null, live: false });
});

test("silence pauses the loop; the first silence says so, later ones stay quiet", () => {
  let r = run(HF, [{ type: "tap" }, { type: "sttDone", transcript: "" }]);
  expect(r.state).toMatchObject({ mode: "idle", live: false, dialogId: "dlg-1" });
  expect(r.commands.filter((c) => c.type === "sayNotUnderstood")).toHaveLength(1);
  r = run(r.state, [{ type: "tap" }, { type: "sttDone", transcript: "y" }, { type: "agentDone", status: "not_understood" }, { type: "speakDone" }, { type: "sttDone", transcript: "" }]);
  expect(r.commands.filter((c) => c.type === "sayNotUnderstood")).toHaveLength(0);
  expect(r.state.mode).toBe("idle");
});

test("cancelled listening is a no-op; cancel drops the turn in flight but keeps the session", () => {
  let r = run(HF, [{ type: "tap" }, { type: "sttDone", transcript: "", cancelled: true }]);
  expect(r.state).toMatchObject({ mode: "idle", live: true, dialogId: "dlg-1" });
  r = run(r.state, [{ type: "text", text: "find parking" }, { type: "cancel" }]);
  expect(r.commands.slice(-2)).toEqual([{ type: "cancelTurn", turnId: 2 }, { type: "stopSpeaking" }]);
  expect(r.state).toMatchObject({ mode: "idle", dialogId: "dlg-1", turnId: 3 });
});

test("a failed turn is a miss and reopens the mic after a pause when live; the second failure ends the dialog", () => {
  let r = run(HF, [{ type: "tap" }, { type: "sttDone", transcript: "x" }, { type: "error" }]);
  expect(r.state).toMatchObject({ mode: "speaking", misses: 1, live: true });
  expect(r.commands.at(-1)).toEqual({ type: "scheduleReopen", afterMs: ERROR_REOPEN_MS });
  r = run(r.state, [{ type: "speakDone" }, { type: "sttDone", transcript: "y" }, { type: "error" }]);
  expect(r.state).toMatchObject({ mode: "idle", dialogId: null });
  // paused loop: a failure does not reopen
  r = run(HF, [{ type: "text", text: "x" }, { type: "error" }]);
  expect(r.state).toMatchObject({ mode: "idle", misses: 1, dialogId: "dlg-2" });
  expect(r.commands.some((c) => c.type === "scheduleReopen")).toBe(false);
});

test("a stale session gets a new id on the next turn; a tick while idle closes it", () => {
  let r = run(HF, [{ type: "tap" }, { type: "sttDone", transcript: "x" }, { type: "agentDone", status: "chat" }, { type: "speakDone" }, { type: "sttDone", transcript: "" }]);
  const later = T0 + SESSION_IDLE_MS + 1;
  expect(isStale(r.state, later)).toBe(true);
  r = run(r.state, [[{ type: "tap" }, later]]);
  expect(r.commands[0]).toEqual({ type: "newDialog", dialogId: "dlg-2" });
  r = run(r.state, [{ type: "sttDone", transcript: "" }]);
  r = run(r.state, [[{ type: "tick" }, later + SESSION_IDLE_MS + 1]]);
  expect(r.state.dialogId).toBeNull();
  expect(r.commands).toEqual([{ type: "closeDialog" }]);
});

test("a nudge offer opens a short window in the offer's session and marks a pending answer", () => {
  let r = run(HF, [{ type: "nudge" }]);
  expect(r.commands).toEqual([{ type: "newDialog", dialogId: "dlg-1" }, { type: "startListening", turnId: 1, chime: true, maxMs: NUDGE_ANSWER_MS }]);
  expect(r.state).toMatchObject({ mode: "listening", pendingConfirm: true });
  // a nudge during a turn does nothing
  r = run(r.state, [{ type: "sttDone", transcript: "yes" }, { type: "nudge" }]);
  expect(r.state.mode).toBe("thinking");
  expect(r.commands.filter((c) => c.type === "startListening")).toHaveLength(0); // the turn in flight wins
});

test("wake barges in: stop speaking, no chime; pause and endRoute", () => {
  let r = run(HF, [{ type: "wake" }]);
  expect(r.commands[0]).toEqual({ type: "stopSpeaking" });
  expect(r.commands.at(-1)).toMatchObject({ type: "startListening", chime: false });
  r = run(r.state, [{ type: "pause" }]);
  expect(r.state.live).toBe(false);
  r = run(r.state, [{ type: "endRoute" }]);
  expect(r.state.dialogId).toBeNull();
  expect(r.commands).toEqual([{ type: "closeDialog" }]);
});


test("an empty transcript from real speech is still a turn; a nudge offer adopts or stamps the session", () => {
  let r = run(HF, [{ type: "tap" }, { type: "sttDone", transcript: "", heard: true }]);
  expect(r.commands.at(-1)).toEqual({ type: "callAgent", turnId: 1, text: "", dialogId: "dlg-1" });
  expect(r.state.mode).toBe("thinking");
  // a stale session is replaced by the offer's; a live one is only stamped
  r = run(HF, [{ type: "adopt", dialogId: "offer-1" }]);
  expect(r.state).toMatchObject({ dialogId: "offer-1", misses: 0, lastTurnAt: T0 });
  expect(r.commands).toEqual([{ type: "newDialog", dialogId: "offer-1" }]);
  const later = T0 + 60_000;
  r = run(r.state, [[{ type: "adopt", dialogId: "offer-1" }, later]]);
  expect(r.state.lastTurnAt).toBe(later);
  r = run(r.state, [[{ type: "adopt", dialogId: "offer-2" }, later + 1]]);
  expect(r.state.dialogId).toBe("offer-1"); // live session kept
  expect(r.commands).toEqual([]);
});

describe("walkie-talkie (the default): the mic opens only on a tap", () => {
  test("an answer does not reopen the mic; the next tap continues the same session", () => {
    let r = run(initialTurnState, [{ type: "tap" }, { type: "sttDone", transcript: "find parking" }, { type: "agentDone", status: "places" }, { type: "speakDone" }]);
    expect(r.state).toMatchObject({ mode: "idle", dialogId: "dlg-1" });
    expect(r.commands.filter((c) => c.type === "startListening")).toHaveLength(1);
    r = run(r.state, [{ type: "tap" }]);
    expect(r.commands).toEqual([{ type: "startListening", turnId: 2, chime: true, maxMs: undefined }]); // no new dialog
  });

  test("a question does not reopen the mic either; 'yes' after a tap lands in the same session", () => {
    let r = run(initialTurnState, [{ type: "text", text: "take me to Payson" }, { type: "agentDone", status: "confirm" }]);
    expect(r.state.pendingConfirm).toBe(true);
    r = run(r.state, [{ type: "speakDone" }]);
    expect(r.state).toMatchObject({ mode: "idle", pendingConfirm: false });
    expect(r.commands).toEqual([]);
    r = run(r.state, [{ type: "tap" }, { type: "sttDone", transcript: "yes" }]);
    expect(r.commands.at(-1)).toEqual({ type: "callAgent", turnId: 2, text: "yes", dialogId: "dlg-1" });
  });

  test("a nudge offer does not open the mic", () => {
    const r = run(initialTurnState, [{ type: "adopt", dialogId: "offer-1" }, { type: "nudge" }]);
    expect(r.commands.filter((c) => c.type === "startListening")).toHaveLength(0);
    expect(r.state).toMatchObject({ mode: "idle", dialogId: "offer-1" });
  });

  test("a failed turn does not reopen the mic", () => {
    const r = run(initialTurnState, [{ type: "tap" }, { type: "sttDone", transcript: "x" }, { type: "error" }]);
    expect(r.state).toMatchObject({ mode: "idle", misses: 1 });
    expect(r.commands.some((c) => c.type === "scheduleReopen")).toBe(false);
  });

  test("every tap that hears nothing says so (not only the first)", () => {
    const r = run(initialTurnState, [{ type: "tap" }, { type: "sttDone", transcript: "" }, { type: "tap" }, { type: "sttDone", transcript: "" }]);
    expect(r.commands.filter((c) => c.type === "sayNotUnderstood")).toHaveLength(2);
    expect(r.state.mode).toBe("idle");
  });
});
