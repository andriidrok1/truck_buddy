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
    playAndWait: jest.fn(async (url: string | object) => void played.push(String(url))),
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
