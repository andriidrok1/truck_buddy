// The copilot's dialog as a pure state machine. Today the same rules live in app/index.tsx as four refs and
// six functions; this module is their single source of truth, unit-tested, and the hook that will run it
// (useCopilotTurn, plan A1.2) only executes the commands it returns. No React, no timers, no I/O.
//
// Where each rule comes from (app/index.tsx on claude/qa-merged-app, 2026-11-02):
// - dialog / live / lastTurnAt / misses refs and endDialog / pauseDialog (L507-518): a route stops the
//   hands-free loop but keeps the session; a fresh session only after end_route, bye, 2 misses or 10 min
//   without a turn (SESSION_IDLE_MS, L84).
// - ensureSession (L520-525): a stale or missing session gets a new id and misses reset; every turn stamps
//   lastTurnAt.
// - continueDialog (L534-543): after an answer, misses = 0 unless not_understood (then +1); end_conversation
//   or 2 misses end the dialog; otherwise the mic reopens only when the loop is live, or when the answer was a
//   confirm question ("Did you say Reno?") which forces one more listen.
// - submitTurn (L845-935): a turn is one request; a failure counts like a null answer (continueDialog with
//   null → a miss) after a 1.2 s pause; a typed turn goes straight to the agent (onSubmitText L829-839 calls
//   ensureSession first).
// - beginListening (L988-1016): ensureSession, live = true, listen; "silent" pauses the loop and, on the very
//   first miss with nothing on screen, says "didn't catch that"; "cancelled" is a no-op; a transcript becomes
//   a turn.
// - cancelTurn (L562-571): the turn in flight is dropped (turn counter bump, abort), the session stays.
// - the nudge poller (L758-811) and listenForAnswer (L1036-1040): an offer opens a short mic window
//   (NUDGE_ANSWER_MS, L90) unless a turn is under way, in the session the offer was made in.
// - controls: stop_talking → pauseDialog, end_route → endDialog (src/state/agentReducer.ts L74, L99).

export const SESSION_IDLE_MS = 10 * 60 * 1000;
export const NUDGE_ANSWER_MS = 7000;
export const ERROR_REOPEN_MS = 1200;
export const MAX_MISSES = 2;

// Walkie-talkie mode (decision 2026-11-04): the mic opens only on a tap. After an answer, after a question
// ("Did you say Reno?"), after a nudge offer and after a failed turn it stays shut; the driver taps to answer,
// and the backend keeps a pending confirm or offer for the next turn of the session, so "yes" still works.
// The hands-free loop below is kept for later (duplex voice, wake word) behind `autoListen`.

export type Mode = "idle" | "listening" | "thinking" | "speaking";

export type TurnState = {
  mode: Mode;
  /** Backend session id (found places, "the second one", rejected set); null = no dialog. */
  dialogId: string | null;
  /** When the last turn or offer landed in this session; staleness is measured from here. */
  lastTurnAt: number | null;
  /** Answers in a row the copilot did not understand (or failed); two end the dialog. */
  misses: number;
  /** Hands-free loop: the mic reopens after each answer (only with autoListen). */
  live: boolean;
  /** Reopen the mic by itself (after answers, questions, nudges, errors). Off = walkie-talkie: taps only. */
  autoListen: boolean;
  /** The last answer was a question ("Did you say Reno?", a nudge offer): one more listen even when paused. */
  pendingConfirm: boolean;
  /** Generation of the turn in flight; a result tagged with an older id is stale. */
  turnId: number;
};

export type TurnEvent =
  | { type: "tap" }
  | { type: "wake" }
  | { type: "text"; text: string }
  /** `heard`: the recogniser ran on real speech but returned no words; the agent still gets the (empty) turn,
   *  as it did before, and answers "didn't catch that". Without it an empty transcript is silence. */
  | { type: "sttDone"; transcript: string; cancelled?: boolean; heard?: boolean }
  /** A nudge offer landed in `dialogId` (the poller's session): adopt it, or just stamp the time when it is ours. */
  | { type: "adopt"; dialogId: string }
  | { type: "agentDone"; status: string; endConversation?: boolean }
  | { type: "speakDone" }
  | { type: "error" }
  | { type: "cancel" }
  | { type: "pause" }
  | { type: "routeSet" }
  | { type: "endRoute" }
  | { type: "nudge" }
  | { type: "tick" };

export type Command =
  | { type: "newDialog"; dialogId: string }
  | { type: "startListening"; turnId: number; chime: boolean; maxMs?: number }
  | { type: "callAgent"; turnId: number; text: string; dialogId: string }
  | { type: "sayNotUnderstood" }
  | { type: "cancelTurn"; turnId: number }
  | { type: "stopSpeaking" }
  | { type: "closeDialog" }
  | { type: "scheduleReopen"; afterMs: number };

export type Reduction = { state: TurnState; commands: Command[] };

export const initialTurnState: TurnState = {
  mode: "idle", dialogId: null, lastTurnAt: null, misses: 0, live: false, autoListen: false, pendingConfirm: false, turnId: 0,
};

export const isStale = (s: TurnState, now: number) => !s.dialogId || s.lastTurnAt === null || now - s.lastTurnAt > SESSION_IDLE_MS;

/** ensureSession: a stale or missing session gets a new id and a clean miss count; every turn stamps the time. */
function ensureSession(s: TurnState, now: number, newId: () => string, commands: Command[]): TurnState {
  if (isStale(s, now)) {
    const dialogId = newId();
    commands.push({ type: "newDialog", dialogId });
    return { ...s, dialogId, misses: 0, lastTurnAt: now };
  }
  return { ...s, lastTurnAt: now };
}

const ended = (s: TurnState): TurnState => ({ ...s, dialogId: null, live: false, misses: 0, pendingConfirm: false });

function listen(s: TurnState, now: number, newId: () => string, chime: boolean, maxMs?: number): Reduction {
  const commands: Command[] = [];
  let next = ensureSession(s, now, newId, commands);
  const turnId = next.turnId + 1;
  next = { ...next, mode: "listening", live: true, pendingConfirm: false, turnId };
  commands.push({ type: "startListening", turnId, chime, maxMs });
  return { state: next, commands };
}

/**
 * One event in, the next state and the commands to run out. `now` is the clock, `newId` mints a session id;
 * both are passed in so the machine stays pure and the tests stay deterministic.
 */
export function reduce(s: TurnState, e: TurnEvent, now: number, newId: () => string): Reduction {
  switch (e.type) {
    case "tap":
      return listen(s, now, newId, true);
    case "wake": {
      // Barge-in: the wake word cuts the copilot off and listens without a second chime.
      const r = listen(s, now, newId, false);
      return { state: r.state, commands: [{ type: "stopSpeaking" }, ...r.commands] };
    }
    case "nudge": {
      // An offer just played: a short window for the "yes", unless a turn is already under way. Walkie-talkie:
      // no window, the offer stays pending on the server and the driver taps to say "yes".
      if (!s.autoListen || s.mode === "listening" || s.mode === "thinking") return { state: s, commands: [] };
      const r = listen(s, now, newId, true, NUDGE_ANSWER_MS);
      return { state: { ...r.state, pendingConfirm: true }, commands: r.commands };
    }
    case "text": {
      const commands: Command[] = [];
      const next = ensureSession(s, now, newId, commands);
      const turnId = next.turnId + 1;
      commands.push({ type: "callAgent", turnId, text: e.text, dialogId: next.dialogId! });
      return { state: { ...next, mode: "thinking", turnId }, commands };
    }
    case "sttDone": {
      if (s.mode !== "listening") return { state: s, commands: [] };
      if (e.cancelled) return { state: { ...s, mode: "idle" }, commands: [] };
      if (!e.transcript && !e.heard) {
        // Silence stops listening. A tap that heard nothing always says so; in the hands-free loop only the very
        // first miss does (the driver may simply not be answering).
        const commands: Command[] = !s.autoListen || s.misses === 0 ? [{ type: "sayNotUnderstood" }] : [];
        return { state: { ...s, mode: "idle", live: false, pendingConfirm: false }, commands };
      }
      return { state: { ...s, mode: "thinking", lastTurnAt: now }, commands: [{ type: "callAgent", turnId: s.turnId, text: e.transcript, dialogId: s.dialogId! }] };
    }
    case "agentDone": {
      if (!s.dialogId) return { state: { ...s, mode: "speaking" }, commands: [] };
      const misses = e.status !== "not_understood" ? 0 : s.misses + 1;
      if (e.endConversation || misses >= MAX_MISSES) {
        return { state: { ...ended(s), mode: "speaking" }, commands: [{ type: "closeDialog" }] };
      }
      return { state: { ...s, mode: "speaking", misses, pendingConfirm: e.status === "confirm", lastTurnAt: now }, commands: [] };
    }
    case "speakDone": {
      if (s.mode !== "speaking") return { state: s, commands: [] };
      if (!s.dialogId || !s.autoListen || (!s.live && !s.pendingConfirm)) return { state: { ...s, mode: "idle", pendingConfirm: false }, commands: [] };
      return listen(s, now, newId, true);
    }
    case "error": {
      // A failed turn counts as a miss; after a short pause the loop goes on if it was live.
      if (!s.dialogId) return { state: { ...s, mode: "idle" }, commands: [] };
      const misses = s.misses + 1;
      if (misses >= MAX_MISSES) return { state: { ...ended(s), mode: "idle" }, commands: [{ type: "closeDialog" }] };
      if (!s.live || !s.autoListen) return { state: { ...s, mode: "idle", misses }, commands: [] };
      return { state: { ...s, mode: "speaking", misses }, commands: [{ type: "scheduleReopen", afterMs: ERROR_REOPEN_MS }] };
    }
    case "cancel": {
      const turnId = s.turnId + 1;
      return { state: { ...s, mode: "idle", turnId }, commands: [{ type: "cancelTurn", turnId: s.turnId }, { type: "stopSpeaking" }] };
    }
    case "pause":
      return { state: { ...s, live: false }, commands: [] };
    case "adopt":
      // The nudge poller made its offer in a session: a stale or missing one is replaced (misses reset), a
      // live one is only stamped so the "yes" does not start a fresh session (index.tsx L795-800).
      if (s.dialogId === e.dialogId) return { state: { ...s, lastTurnAt: now }, commands: [] };
      if (!isStale(s, now)) return { state: s, commands: [] };
      return { state: { ...s, dialogId: e.dialogId, misses: 0, lastTurnAt: now }, commands: [{ type: "newDialog", dialogId: e.dialogId }] };
    case "routeSet":
      // A route stops the hands-free loop but keeps the session (follow-ups land on the same places).
      return { state: { ...s, live: false }, commands: [] };
    case "endRoute":
      return { state: { ...ended(s), mode: s.mode === "speaking" ? "speaking" : "idle" }, commands: s.dialogId ? [{ type: "closeDialog" }] : [] };
    case "tick":
      if (s.mode === "idle" && s.dialogId && isStale(s, now)) return { state: ended(s), commands: [{ type: "closeDialog" }] };
      return { state: s, commands: [] };
  }
}
