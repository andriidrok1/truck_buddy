/** Rows of the inverted history list, newest first: each turn, and after the first turn of a day (so above it on
 *  screen) that day's label as a row of its own. Inside one row the label sat under the message on Android,
 *  where the inverted list flips the cell's children too (QA 2026-11-02). */
export type HistoryRow<T> = { kind: "turn"; turn: T; key: string } | { kind: "day"; at: string; key: string };

const dayKey = (iso: string) => new Date(iso).toDateString();

export function historyRows<T extends { at: string }>(oldestFirst: readonly T[]): HistoryRow<T>[] {
  const rows: HistoryRow<T>[] = [];
  for (let i = oldestFirst.length - 1; i >= 0; i--) {
    const t = oldestFirst[i];
    rows.push({ kind: "turn", turn: t, key: `${t.at}-${i}` });
    const older = oldestFirst[i - 1];
    if (!older || dayKey(older.at) !== dayKey(t.at)) rows.push({ kind: "day", at: t.at, key: `day-${dayKey(t.at)}` });
  }
  return rows;
}
