import { historyRows } from "../utils/historyRows";

test("newest first, each day's label right after its first turn (above it in the inverted list)", () => {
  const turns = [
    { at: "2026-11-01T18:00:00", text: "a" },
    { at: "2026-11-02T09:00:00", text: "b" },
    { at: "2026-11-02T09:01:00", text: "c" },
  ];
  expect(historyRows(turns).map((r) => (r.kind === "turn" ? r.turn.text : `day ${r.at.slice(0, 10)}`))).toEqual([
    "c", "b", "day 2026-11-02", "a", "day 2026-11-01",
  ]);
  expect(historyRows([])).toEqual([]);
});
