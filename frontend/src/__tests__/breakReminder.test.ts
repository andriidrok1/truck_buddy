import { breakDue } from "../hooks/useBreakReminder";

const H = 3600 * 1000;
const T0 = 1_700_000_000_000;

describe("breakDue", () => {
  test("a route in preview is never due, however long it has been there", () => {
    expect(breakDue(null, 0, T0 + 20 * H, 4 * H)).toBe(false);
  });

  test("the time counts from Go", () => {
    expect(breakDue(T0, 0, T0 + 4 * H - 1, 4 * H)).toBe(false);
    expect(breakDue(T0, 0, T0 + 4 * H, 4 * H)).toBe(true);
  });

  test("after a reminder the next one is a full interval later", () => {
    expect(breakDue(T0, T0 + 4 * H, T0 + 5 * H, 4 * H)).toBe(false);
    expect(breakDue(T0, T0 + 4 * H, T0 + 8 * H, 4 * H)).toBe(true);
  });

  test("a new Go after a stop starts the count again, whatever the last reminder was", () => {
    expect(breakDue(T0 + 10 * H, T0 + 4 * H, T0 + 11 * H, 4 * H)).toBe(false);
    expect(breakDue(T0 + 10 * H, T0 + 4 * H, T0 + 14 * H, 4 * H)).toBe(true);
  });
});
