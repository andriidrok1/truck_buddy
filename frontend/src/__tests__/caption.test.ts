import { lineCaption, replyCaption, replyEnded, REPLY_HOLD_MS } from "../dialog/caption";

describe("caption", () => {
  test("a turn call queued behind a long answer does not cover it right after it was said", () => {
    const answer = replyCaption("Got it, 48-foot trailer. I'll keep that in mind.", undefined, 0);
    const ended = replyEnded(answer, 5000); // the answer took 5 s to say
    expect(lineCaption("In 500 feet, turn left", ended, 6000)).toBe(ended);
    expect(lineCaption("In 500 feet, turn left", ended, 5000 + REPLY_HOLD_MS)).toEqual({ reply: "In 500 feet, turn left", kind: "line", at: 5000 + REPLY_HOLD_MS });
  });
  test("only the answer's own end starts the hold", () => {
    const ended = replyEnded(replyCaption("Got it.", undefined, 0), 2000);
    expect(replyEnded(ended, 9000)).toBe(ended);
    expect(lineCaption("Turn left", ended, 2000 + REPLY_HOLD_MS).reply).toBe("Turn left");
  });
  test("one line replaces another at once", () => {
    const first = lineCaption("Rerouting.", null, 0);
    expect(lineCaption("In 500 feet, turn left", first, 100).reply).toBe("In 500 feet, turn left");
  });
  test("the end of a spoken line changes nothing", () => {
    const line = lineCaption("Rerouting.", null, 0);
    expect(replyEnded(line, 4000)).toBe(line);
    expect(replyEnded(null)).toBeNull();
  });
});
