// The copilot's dialog on the device: runs src/dialog/turnMachine.ts and executes its commands (listen,
// transcribe, call the agent, reopen the mic) through injected dependencies, so the map screen only
// supplies what the commands need and applies the answer. One AbortController per turn replaces the
// screen's global turn counter for everything that belongs to the dialog (plan A1.2).

import { useEffect, useState, useSyncExternalStore } from "react";

import { ApiError } from "../api";
import { MESSAGES } from "../constants";
import type { ActiveRoute, AgentResponse, LatLng, UiState } from "../types";
import type { FillerPlan } from "../voice/fillers";
import { buildSttHint } from "../voice/sttHint";
import { initialTurnState, isStale, reduce, type Command, type TurnEvent, type TurnState } from "./turnMachine";

export const REQUEST_TIMEOUT_MS = 60000;
export const STAGES = ["Searching nearby…", "Setting route…"];
export const STAGE_MS = 3500;
export const NO_CONNECTION = "I can't reach the server right now. Check your signal and try again.";
export const STT_SLOW = "Speech recognition is slow right now. Give it a moment and try again.";
export const RATE_LIMITED = "One second, let me catch up.";

export const newSessionId = () => `dlg-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;

/** What the agent is told about the moment of the turn; read when the call is made, never earlier. */
export type TurnContext = {
  coords: LatLng;
  active: ActiveRoute | null;
  ui: UiState;
  heading: number | null;
  speed: number | null;
  limitMph: number | null;
  /** Names the recogniser should expect (what is on screen and on the route), most useful first. */
  hintNames: readonly (string | null | undefined)[];
};

/** Handed to the screen with each answer; `isCurrent` is the old `myTurn === turn.current`. */
export type AnswerContext = {
  turnId: number;
  signal: AbortSignal;
  /** Resolves once the acknowledgement clip (if any) is done: the reply waits for it. */
  afterFiller: Promise<void>;
  isCurrent: () => boolean;
};

export type TurnDeps = {
  voice: {
    /** The recording's uri; `{uri, maybe}` when the mic heard something but no clear speech; or "silent" / "cancelled". */
    listen: (opts: { chime: boolean; maxMs?: number }) => Promise<string | { uri: string; maybe: true }>;
    cancelRecording: () => Promise<void>;
    stopSpeaking: () => void;
  };
  api: {
    transcribe: (uri: string, signal?: AbortSignal, hint?: string, maybe?: boolean) => Promise<{ transcript: string; stt_ms?: number | null; stt_confidence?: number | null }>;
    agentSpeak: (
      text: string, at: LatLng, active: ActiveRoute | null, signal: AbortSignal | undefined, sessionId: string | undefined,
      ui: UiState, heading: number | null, limitMph: number | null, sttMs: number | null, sttConfidence: number | null,
      speedMps: number | null,
    ) => Promise<AgentResponse>;
  };
  fillers: { schedule: () => FillerPlan; noteTurn: (totalMs: number) => void };
  /** True when the turn was answered offline by the caller (said and shown); the dialog pauses. */
  offline: () => boolean;
  /** Is there a signal right now. Unlike `offline` it says and shows nothing: for checks that are not a turn. */
  isOnline: () => boolean;
  context: () => TurnContext;
  /** The phase the sheet shows while the copilot listens or works. */
  onPhase: (phase: "listening" | "thinking") => void;
  /** The turn is over without an answer on the sheet: the screen goes back to what it was showing. */
  onIdle: () => void;
  onThinking: (text: string | undefined) => void;
  onHeard: (transcript: string) => void;
  /** Silence on the very first miss: "didn't catch that" on the caption (only if nothing is shown). */
  onNotUnderstood: () => void;
  onError: (message: string) => void;
  onListenFailed: () => void;
  /** Applies the answer (state patch, effects, speech); resolves with the promise of the spoken reply, wrapped
   *  so the await here does not flatten it (the reply may play for a while after the effects ran). */
  onResponse: (res: AgentResponse, ctx: AnswerContext) => Promise<{ spoken: Promise<void> }>;
  /** Hands-free loop (the mic reopens by itself). Off by default: walkie-talkie, taps only (turnMachine.ts). */
  autoListen?: boolean;
  now?: () => number;
  newId?: () => string;
};

export type CopilotTurn = {
  getState: () => TurnState;
  /** The mic button: listen (cuts a turn in flight, same session). */
  tap: () => void;
  /** A typed request: same session, no mic afterwards. */
  text: (text: string) => void;
  /** Drop the turn in flight; the session stays. */
  cancel: () => void;
  /** Stop the hands-free loop (Go, stop_talking); the session stays. */
  pause: () => void;
  routeSet: () => void;
  /** End route / bye: the session is over. */
  endRoute: () => void;
  /** A nudge just played: a short mic window for the answer. */
  nudgeOffer: () => void;
  /** The nudge poller made its offer in this session. */
  adoptSession: (dialogId: string) => void;
  /** A later answer of the same turn (breakdown research): misses and the loop follow it. */
  answered: (status: string, endConversation?: boolean) => void;
  dialogId: () => string | null;
  isStale: () => boolean;
  isCurrent: (turnId: number) => boolean;
  turnId: () => number;
};

/** What a failed turn says: by HTTP status, else by the error's shape. */
export function errorMessage(e: unknown): string {
  const status = e instanceof ApiError ? e.status : null;
  if (status === 422) return "Check your truck profile and try again.";
  if (status === 502) return MESSAGES.placesUnavailable;
  if (status === 504) return STT_SLOW;
  if (status === 429) return RATE_LIMITED;
  if (status === null && /abort|network|timed? ?out|failed to fetch/i.test(String(e))) return NO_CONNECTION;
  return MESSAGES.notUnderstood;
}

type Prepared = { ctrl: AbortController; plan: FillerPlan | null; sttMs: number | null; sttConfidence: number | null; timer: ReturnType<typeof setTimeout> };

export type CopilotTurnController = CopilotTurn & {
  subscribe: (listener: () => void) => () => void;
  setDeps: (deps: TurnDeps) => void;
  /** Drops timers and the request in flight (unmount). */
  dispose: () => void;
};

/**
 * The dialog controller: runs the machine, executes its commands through `deps`, and notifies subscribers
 * of every state change. Framework-free, so it is tested without React; useCopilotTurn is a thin binding.
 */
export function createCopilotTurn(initialDeps: TurnDeps): CopilotTurnController {
  let deps = initialDeps;
  let state: TurnState = { ...initialTurnState, autoListen: !!initialDeps.autoListen };
  const listeners = new Set<() => void>();
  let inflight: Prepared | null = null; // the request of the turn in flight
  let prepared: Prepared | null = null; // STT done, agent call about to start (same turn)
  let stageTimer: ReturnType<typeof setInterval> | null = null;
  let reopen: ReturnType<typeof setTimeout> | null = null;
  const now = () => deps.now?.() ?? Date.now();
  const newId = () => deps.newId?.() ?? newSessionId();
  const isCurrent = (turnId: number) => state.turnId === turnId;

  const clearStages = () => {
    if (stageTimer) clearInterval(stageTimer);
    stageTimer = null;
  };
  const startStages = () => {
    clearStages();
    let i = 0;
    deps.onThinking(STAGES[0]);
    stageTimer = setInterval(() => {
      i = Math.min(i + 1, STAGES.length - 1);
      deps.onThinking(STAGES[i]);
    }, STAGE_MS);
  };
  const dropRequest = (p: Prepared | null) => {
    if (!p) return;
    clearTimeout(p.timer);
    p.ctrl.abort();
    p.plan?.cancel();
  };
  const prepare = (plan: FillerPlan | null): Prepared => {
    const ctrl = new AbortController();
    return { ctrl, plan, sttMs: null, sttConfidence: null, timer: setTimeout(() => ctrl.abort(), REQUEST_TIMEOUT_MS) };
  };

  function dispatch(e: TurnEvent): TurnState {
    const { state: next, commands } = reduce(state, e, now(), newId);
    state = next;
    listeners.forEach((l) => l());
    for (const c of commands) execute(c);
    return next;
  }

  async function runListening(c: Extract<Command, { type: "startListening" }>) {
    deps.onPhase("listening");
    clearStages();
    let res: string | { uri: string; maybe: true };
    try {
      res = await deps.voice.listen({ chime: c.chime, maxMs: c.maxMs });
    } catch {
      if (!isCurrent(c.turnId)) return;
      deps.onListenFailed();
      dispatch({ type: "pause" });
      dispatch({ type: "sttDone", transcript: "", cancelled: true });
      deps.onIdle();
      return;
    }
    if (!isCurrent(c.turnId)) return;
    if (res === "cancelled") {
      dispatch({ type: "sttDone", transcript: "", cancelled: true });
      return;
    }
    if (res === "silent") {
      dispatch({ type: "sttDone", transcript: "" });
      deps.onIdle();
      return;
    }
    // A recording: the transcript is the turn. The acknowledgement is planned before STT, so the driver
    // hears something ~300 ms after they stop talking (the transcript alone takes 1-1.5 s to come back).
    // A doubtful recording (no clear speech) gets no "Hang on.": most of them turn out empty.
    const maybe = typeof res !== "string";
    const uri = typeof res === "string" ? res : res.uri;
    deps.onPhase("thinking");
    startStages();
    const p = prepare(maybe ? null : deps.fillers.schedule());
    prepared = p;
    try {
      const ctx = deps.context();
      const hint = buildSttHint(ctx.hintNames);
      const stt = await deps.api.transcribe(uri, p.ctrl.signal, hint || undefined, maybe);
      if (!isCurrent(c.turnId) || prepared !== p) return;
      p.sttMs = stt.stt_ms ?? null;
      p.sttConfidence = stt.stt_confidence ?? null;
      if (!stt.transcript) p.plan?.cancel(); // nothing heard: no "Hang on." for silence
      else deps.onHeard(stt.transcript);
      if (maybe && !stt.transcript) {
        // Noise, not words: the same as silence (no agent call, "didn't hear anything").
        prepared = null;
        dropRequest(p);
        clearStages();
        dispatch({ type: "sttDone", transcript: "" });
        deps.onIdle();
        return;
      }
      dispatch({ type: "sttDone", transcript: stt.transcript, heard: true });
    } catch (e) {
      if (!isCurrent(c.turnId) || prepared !== p) return;
      prepared = null;
      dropRequest(p);
      clearStages();
      deps.onError(errorMessage(e));
      deps.onIdle();
      dispatch({ type: "error" });
    }
  }

  async function runAgent(c: Extract<Command, { type: "callAgent" }>) {
    // The STT step prepared the request (filler, controller); a typed turn starts from nothing.
    const p = prepared && isCurrent(c.turnId) ? prepared : prepare(null);
    prepared = null;
    inflight = p;
    deps.onPhase("thinking");
    if (!stageTimer) startStages();
    try {
      const ctx = deps.context();
      const res = await deps.api.agentSpeak(c.text, ctx.coords, ctx.active, p.ctrl.signal, c.dialogId, ctx.ui, ctx.heading, ctx.limitMph, p.sttMs, p.sttConfidence, ctx.speed);
      if (!isCurrent(c.turnId)) return;
      // The agent answered: the turn's 60 s budget (STT + agent) is spent. What follows keeps the signal for a
      // barge-in or a cancel, but not the timer: a breakdown research has its own 120 s budget
      // (api.breakdownResult), and the 60 s cut it off mid-way with "I can't reach the server".
      clearTimeout(p.timer);
      deps.fillers.noteTurn(res.timings?.total ?? 0);
      // Fast answer: the filler never starts. Otherwise the answer waits for it to finish.
      p.plan?.cancel();
      const afterFiller = p.plan?.done ?? Promise.resolve();
      clearStages();
      dispatch({ type: "agentDone", status: res.status, endConversation: !!res.end_conversation });
      const { spoken } = await deps.onResponse(res, { turnId: c.turnId, signal: p.ctrl.signal, afterFiller, isCurrent: () => isCurrent(c.turnId) });
      await spoken.catch(() => undefined);
      if (isCurrent(c.turnId) && state.mode === "speaking") dispatch({ type: "speakDone" });
    } catch (e) {
      p.plan?.cancel();
      if (!isCurrent(c.turnId)) return; // cancelled by the driver (barge-in, close): nothing to report
      clearStages();
      deps.onThinking(undefined);
      deps.onError(errorMessage(e));
      deps.onIdle();
      dispatch({ type: "error" });
    } finally {
      clearTimeout(p.timer);
      if (inflight === p) inflight = null;
    }
  }

  function execute(c: Command) {
    switch (c.type) {
      case "newDialog":
      case "closeDialog":
        return;
      case "startListening":
        void runListening(c);
        return;
      case "callAgent":
        void runAgent(c);
        return;
      case "sayNotUnderstood":
        deps.onNotUnderstood();
        return;
      case "cancelTurn":
        dropRequest(inflight);
        dropRequest(prepared);
        inflight = null;
        prepared = null;
        clearStages();
        deps.onThinking(undefined);
        return;
      case "stopSpeaking":
        deps.voice.stopSpeaking();
        return;
      case "scheduleReopen": {
        if (reopen) clearTimeout(reopen);
        const turnId = state.turnId;
        reopen = setTimeout(() => {
          reopen = null;
          if (isCurrent(turnId) && state.mode === "speaking") dispatch({ type: "speakDone" });
        }, c.afterMs);
        return;
      }
    }
  }

  return {
    getState: () => state,
    subscribe: (l) => {
      listeners.add(l);
      return () => {
        listeners.delete(l);
      };
    },
    setDeps: (d) => {
      deps = d;
    },
    dispose: () => {
      clearStages();
      if (reopen) clearTimeout(reopen);
      dropRequest(inflight);
      dropRequest(prepared);
      inflight = null;
      prepared = null;
    },
    tap: () => {
      if (deps.offline()) {
        dispatch({ type: "pause" });
        deps.onIdle();
        return;
      }
      // Barge-in: a tap while the copilot is still working drops that turn (same session). While the words are
      // being transcribed the machine is still "listening", but the STT request and its "Hang on." plan are
      // already under way (`prepared`): left alone, "Still looking." played over the new recording.
      if (state.mode === "thinking" || prepared) dispatch({ type: "cancel" });
      dispatch({ type: "tap" });
    },
    text: (t) => {
      if (state.mode === "thinking") dispatch({ type: "cancel" });
      deps.voice.cancelRecording().catch(() => {});
      dispatch({ type: "pause" });
      if (deps.offline()) {
        deps.onIdle();
        return;
      }
      dispatch({ type: "text", text: t });
    },
    cancel: () => {
      dispatch({ type: "cancel" });
    },
    pause: () => {
      dispatch({ type: "pause" });
    },
    routeSet: () => {
      dispatch({ type: "routeSet" });
    },
    endRoute: () => {
      dispatch({ type: "endRoute" });
    },
    nudgeOffer: () => {
      // Not `offline()`: that one answers a turn ("No signal", spoken and shown), and nobody asked anything here.
      if (!deps.isOnline()) return;
      dispatch({ type: "nudge" });
    },
    adoptSession: (dialogId) => {
      dispatch({ type: "adopt", dialogId });
    },
    answered: (status, endConversation = false) => {
      dispatch({ type: "agentDone", status, endConversation });
    },
    dialogId: () => state.dialogId,
    isStale: () => isStale(state, now()),
    isCurrent,
    turnId: () => state.turnId,
  };
}

/**
 * The controller bound to a component: one per mount, deps refreshed after every render. `turn` is the same
 * object for the life of the component (safe in effect dependency lists); `state` is the current snapshot.
 */
export function useCopilotTurn(deps: TurnDeps): { turn: CopilotTurnController; state: TurnState } {
  const [turn] = useState(() => createCopilotTurn(deps));
  useEffect(() => {
    turn.setDeps(deps);
  });
  useEffect(() => () => turn.dispose(), [turn]);
  const state = useSyncExternalStore(turn.subscribe, turn.getState, turn.getState);
  return { turn, state };
}
