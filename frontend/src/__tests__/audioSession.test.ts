import { Platform } from "react-native";

jest.mock("expo-audio", () => ({ setIsAudioActiveAsync: jest.fn(() => Promise.resolve()) }));

// A fresh module per test (its queue is module state), with the mock that module sees.
const load = () => {
  jest.resetModules();
  const calls = require("expo-audio").setIsAudioActiveAsync as jest.Mock;
  const session = require("../hooks/audioSession");
  return { calls, ...session };
};

describe("audio session on iOS", () => {
  beforeEach(() => {
    Platform.OS = "ios";
  });

  test("a cut reply releases the session; the next sound turns it on only after the release landed", async () => {
    const { calls, acquireAudioSession, releaseAudioSession } = load();
    let land: () => void = () => {};
    calls.mockImplementationOnce(() => new Promise<void>((r) => (land = r))); // the release is slow
    releaseAudioSession();
    const next = acquireAudioSession();
    for (let i = 0; i < 5; i++) await Promise.resolve();
    expect(calls.mock.calls).toEqual([[false]]); // not turned on before the release finished
    land();
    await next;
    expect(calls.mock.calls).toEqual([[false], [true]]);
  });

  test("nothing released, nothing to turn on; Android untouched", async () => {
    const { calls, acquireAudioSession, releaseAudioSession } = load();
    await acquireAudioSession();
    expect(calls).not.toHaveBeenCalled();
    Platform.OS = "android";
    releaseAudioSession();
    await acquireAudioSession();
    expect(calls).not.toHaveBeenCalled();
  });
});
