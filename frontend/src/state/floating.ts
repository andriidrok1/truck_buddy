/** The round 56 pt buttons that float over the map (Follow truck) must never sit on the top bar's buttons
 *  (history, tester): with a tall route card the map window shrank and Follow covered 17 pt of the history
 *  button (UI review 2026-11-05, third pass, M3). */
export const FLOATING_SIZE = 56;
/** The gap kept between a floating button and the top bar. */
export const FLOATING_GAP = 8;

/** The `bottom` for a floating button that wants to sit at `bottom`, or null when it would reach into the top
 *  bar (`topH`, which includes the history and tester buttons); the button is not drawn then. */
export const floatingBottom = ({ windowH, topH, bottom, size = FLOATING_SIZE }: { windowH: number; topH: number; bottom: number; size?: number }): number | null =>
  windowH - bottom - size >= topH + FLOATING_GAP ? bottom : null;
