import { api } from "../api";
import { NO_SIGNAL_TURN_LINE } from "../geo/offlineGuidance";
import { offlineTurn } from "../state/offlineTurn";
import { PRIORITY } from "../voice/queue";

jest.mock("../api", () => ({
  absoluteUrl: (u: string) => u,
  api: { transcribe: jest.fn(), agentSpeak: jest.fn(), speak: jest.fn() },
}));

describe("offlineTurn", () => {
  beforeEach(() => jest.clearAllMocks());

  test("offline: the line is shown and spoken (system, key no-signal), nothing is transcribed or asked", () => {
    const say = jest.fn();
    const setCaption = jest.fn();
    expect(offlineTurn({ isOnline: () => false, say, setCaption })).toBe(true);
    expect(setCaption).toHaveBeenCalledWith({ reply: NO_SIGNAL_TURN_LINE });
    expect(say).toHaveBeenCalledTimes(1);
    expect(say).toHaveBeenCalledWith(NO_SIGNAL_TURN_LINE, expect.objectContaining({ priority: PRIORITY.MANEUVER, kind: "system", key: "no-signal" }));
    expect(api.transcribe).not.toHaveBeenCalled();
    expect(api.agentSpeak).not.toHaveBeenCalled();
    expect(api.speak).not.toHaveBeenCalled();
  });

  test("online: nothing happens and the turn goes on", () => {
    const say = jest.fn();
    const setCaption = jest.fn();
    expect(offlineTurn({ isOnline: () => true, say, setCaption })).toBe(false);
    expect(say).not.toHaveBeenCalled();
    expect(setCaption).not.toHaveBeenCalled();
  });
});
