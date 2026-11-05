import { expireLine, hasExpiringLine, lineCaption, replyCaption } from "../dialog/caption";

describe("caption", () => {
  test("a spoken line never replaces the answer, however long after it comes", () => {
    const answer = replyCaption("Alright, got one. Harbor Truck and Auto Plaza, right off Harbor Blvd, about 11 minutes.", "where can I park tonight", 0);
    const later = lineCaption("Parking looks good there.", answer, 60_000);
    expect(later.reply).toBe(answer.reply);
    expect(later.heard).toBe("where can I park tonight");
    expect(later.kind).toBe("reply");
    expect(later.note).toBe("Parking looks good there.");
  });

  test("the note under an answer expires, the answer stays", () => {
    const answer = replyCaption("Got it, 48-foot trailer.", undefined, 0);
    expect(hasExpiringLine(answer)).toBe(false);
    expect(expireLine(answer)).toBe(answer);
    const withNote = lineCaption("In 500 feet, turn left", answer, 5000);
    expect(hasExpiringLine(withNote)).toBe(true);
    const after = expireLine(withNote);
    expect(after?.reply).toBe("Got it, 48-foot trailer.");
    expect(after?.note).toBeUndefined();
    expect(hasExpiringLine(after)).toBe(false);
  });

  test("a new answer replaces the old one and drops its note", () => {
    const first = lineCaption("Rerouting.", replyCaption("First answer.", undefined, 0), 100);
    const second = replyCaption("Second answer.", undefined, 200);
    expect(second.reply).toBe("Second answer.");
    expect(second.note).toBeUndefined();
    expect(first.reply).toBe("First answer.");
  });

  test("lines without an answer replace each other and then go", () => {
    const first = lineCaption("Rerouting.", null, 0);
    const second = lineCaption("In 500 feet, turn left", first, 100);
    expect(second).toEqual({ reply: "In 500 feet, turn left", kind: "line", at: 100 });
    expect(hasExpiringLine(second)).toBe(true);
    expect(expireLine(second)).toBeNull();
    expect(expireLine(null)).toBeNull();
  });
});
