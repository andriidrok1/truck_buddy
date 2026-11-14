// The hook that plays the speech queue: what happens around the /api/speak round trip (deadline, the phone's
// own voice, a line that went stale meanwhile). The queue's own rules are in speechQueue.test.ts.
import { act, renderHook } from "@testing-library/react-native";

import { api } from "../api";
import { MANEUVER_SPEAK_DEADLINE_MS, SPEAK_DEADLINE_MS, useSpeechQueue } from "../hooks/useSpeechQueue";
import { speakLocal } from "../voice/localTts";
import { PRIORITY, type DropReason, type QueuedSpeech } from "../voice/queue";

jest.mock("../api", () => ({
  absoluteUrl: (u: string) => `https://ci.invalid${u}`,
  api: { speak: jest.fn() },
}));
jest.mock("../voice/localTts", () => ({ speakLocal: jest.fn(async () => undefined), stopLocal: jest.fn() }));
jest.mock("../hooks/useOnline", () => ({ isOnline: () => true }));

const speak = api.speak as jest.Mock;
const NOW = { priority: PRIORITY.MANEUVER_NOW, ttlMs: 8000, key: "m-1-now", kind: "maneuver" } as const;
const SYSTEM = { priority: PRIORITY.MANEUVER, ttlMs: 20000, key: "reroute", kind: "system" } as const;

/** A /speak call the test answers (or fails) when it wants to. */
function pendingSpeak() {
  let resolve: (v: { audio_url: string }) => void = () => {};
  let reject: (e: unknown) => void = () => {};
  speak.mockImplementationOnce(() => new Promise((res, rej) => ((resolve = res), (reject = rej))));
  return { resolve: () => resolve({ audio_url: "/audio/a.mp3" }), reject: () => reject(new Error("aborted")) };
}

async function setup() {
  const state = { busy: false, recording: false, speaking: false };
  const played: string[] = [];
  const shown: string[] = [];
  const done: string[] = [];
  const drops: [string, DropReason][] = [];
  const voice = {
    playAndWait: jest.fn(async (url: string | object): Promise<void> => {
      played.push(String(url));
    }),
    stopSpeaking: jest.fn(),
    isSpeaking: () => state.speaking,
    isRecording: () => state.recording,
    holdPlayback: jest.fn(),
  };
  const h = await renderHook(() =>
    useSpeechQueue(
      voice as never,
      () => state.busy,
      (item: QueuedSpeech) => shown.push(item.text),
      (item: QueuedSpeech) => done.push(item.text),
      (item: QueuedSpeech, why: DropReason) => drops.push([item.text, why]),
    ),
  );
  return { say: h.result.current.say, queue: h.result.current.queue, state, voice, played, shown, done, drops };
}

const settle = () => act(async () => undefined);

beforeEach(() => {
  jest.useFakeTimers();
  jest.clearAllMocks();
  speak.mockResolvedValue({ audio_url: "/audio/a.mp3" });
});
afterEach(() => jest.useRealTimers());

test("a line asks /speak with a short deadline, shorter for a maneuver", async () => {
  const q = await setup();
  q.say("Turn right.", NOW);
  await settle();
  q.say("Rerouting.", SYSTEM);
  await settle();
  expect(speak.mock.calls).toEqual([["Turn right.", MANEUVER_SPEAK_DEADLINE_MS], ["Rerouting.", SPEAK_DEADLINE_MS]]);
  expect(MANEUVER_SPEAK_DEADLINE_MS).toBeLessThan(SPEAK_DEADLINE_MS);
  expect(SPEAK_DEADLINE_MS).toBeLessThanOrEqual(5000);
  expect(q.played).toEqual(["https://ci.invalid/audio/a.mp3", "https://ci.invalid/audio/a.mp3"]);
  expect(q.done).toEqual(["Turn right.", "Rerouting."]);
});

test("the deadline ran out: the phone's own voice says the line", async () => {
  const q = await setup();
  const cloud = pendingSpeak();
  q.say("Turn right.", NOW);
  await settle();
  expect(q.shown).toEqual([]); // nothing on air yet: no caption
  jest.advanceTimersByTime(MANEUVER_SPEAK_DEADLINE_MS);
  cloud.reject(); // what request() does when its deadline aborts the fetch
  await settle();
  expect(speakLocal).toHaveBeenCalledWith("Turn right.", expect.any(Number));
  expect(q.voice.playAndWait).not.toHaveBeenCalled();
  expect(q.shown).toEqual(["Turn right."]);
  expect(q.done).toEqual(["Turn right."]);
});

test("a line whose TTL passed while the audio was fetched is dropped, not said late", async () => {
  const q = await setup();
  const cloud = pendingSpeak();
  q.say("Turn right.", NOW);
  await settle();
  jest.advanceTimersByTime(8000);
  cloud.resolve();
  await settle();
  expect(q.voice.playAndWait).not.toHaveBeenCalled();
  expect(speakLocal).not.toHaveBeenCalled();
  expect(q.shown).toEqual([]);
  expect(q.done).toEqual([]);
  expect(q.drops).toEqual([["Turn right.", "expired"]]);
});

test("the driver tapped the mic while the audio was fetched: the line waits and is said after", async () => {
  const q = await setup();
  const cloud = pendingSpeak();
  q.say("Rerouting.", SYSTEM);
  await settle();
  q.state.recording = true; // the mic opened during the /speak round trip
  cloud.resolve();
  await settle();
  expect(q.voice.playAndWait).not.toHaveBeenCalled();
  expect(q.shown).toEqual([]);
  expect(q.queue.pending()).toBe(1);
  q.state.recording = false;
  q.state.speaking = true; // the copilot's reply is on air now
  await act(async () => void jest.advanceTimersByTime(500));
  expect(q.voice.playAndWait).not.toHaveBeenCalled();
  q.state.speaking = false;
  await act(async () => void jest.advanceTimersByTime(500));
  expect(q.played).toEqual(["https://ci.invalid/audio/a.mp3"]);
  expect(q.shown).toEqual(["Rerouting."]);
  expect(q.done).toEqual(["Rerouting."]);
  expect(q.drops).toEqual([]);
});

test("a turn call held back by a turn in progress expires instead of playing late", async () => {
  const q = await setup();
  const cloud = pendingSpeak();
  q.say("Turn right.", NOW);
  await settle();
  q.state.busy = true; // listening / thinking
  cloud.resolve();
  await settle();
  await act(async () => void jest.advanceTimersByTime(8000));
  q.state.busy = false;
  await act(async () => void jest.advanceTimersByTime(500));
  expect(q.voice.playAndWait).not.toHaveBeenCalled();
  expect(q.drops).toEqual([["Turn right.", "expired"]]);
});

test("a parking line with the server's clip waits for the turn call on air and plays that clip", async () => {
  const q = await setup();
  let endTurnCall: () => void = () => {};
  q.voice.playAndWait.mockImplementationOnce((url: string | object) => {
    q.played.push(String(url));
    return new Promise<void>((r) => (endTurnCall = r));
  });
  q.say("In half a mile, turn right.", { priority: PRIORITY.MANEUVER, ttlMs: 30000, key: "m-1", kind: "maneuver" });
  await settle();
  q.say("Parking looks good there.", { priority: PRIORITY.MANEUVER, kind: "system", key: "parking-verdict", ttlMs: 30000, audioUrl: "https://ci.invalid/audio/verdict.mp3" });
  await settle();
  expect(q.voice.stopSpeaking).not.toHaveBeenCalled(); // the turn call is not cut
  expect(q.shown).toEqual(["In half a mile, turn right."]);
  endTurnCall();
  await settle();
  expect(q.done).toEqual(["In half a mile, turn right.", "Parking looks good there."]);
  expect(q.played).toEqual(["https://ci.invalid/audio/a.mp3", "https://ci.invalid/audio/verdict.mp3"]);
  expect(speak).toHaveBeenCalledTimes(1); // the verdict's clip was already made
  expect(q.shown).toEqual(["In half a mile, turn right.", "Parking looks good there."]);
});

test("a parking verdict whose destination changed while it waited is dropped, not said about the old place", async () => {
  const q = await setup();
  let destination = "A";
  q.state.busy = true; // the driver is asking for another place
  q.say("Parking looks tight there.", {
    priority: PRIORITY.MANEUVER, kind: "system", key: "parking-verdict", ttlMs: 60000, audioUrl: "https://ci.invalid/audio/verdict.mp3",
    valid: () => destination === "A",
  });
  await settle();
  expect(q.played).toEqual([]);
  destination = "B";
  q.state.busy = false;
  await act(async () => {
    jest.advanceTimersByTime(600); // the idle poll takes the line again
  });
  await settle();
  expect(q.played).toEqual([]);
  expect(q.shown).toEqual([]);
  expect(q.drops).toEqual([["Parking looks tight there.", "expired"]]);
});

test("a line held back after its audio was made keeps the clip: one /speak call in all", async () => {
  const q = await setup();
  const first = pendingSpeak();
  q.say("Rerouting.", SYSTEM);
  await settle();
  q.state.busy = true; // the mic opened while the audio was being made
  first.resolve();
  await settle();
  expect(q.played).toEqual([]);
  q.state.busy = false;
  await act(async () => {
    jest.advanceTimersByTime(600);
  });
  await settle();
  expect(q.played).toEqual(["https://ci.invalid/audio/a.mp3"]);
  expect(speak).toHaveBeenCalledTimes(1);
});
