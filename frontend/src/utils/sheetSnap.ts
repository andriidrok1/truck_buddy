// Geometry of the draggable copilot sheet: two resting heights (the content's own height and a
// tall state that leaves the top of the map visible) and the rules for the drag in between.
// Pure functions so the snap decision can be tested without a gesture; every one is a worklet
// because the gesture callbacks call them on the UI thread.

export type SheetHeights = { collapsed: number; expanded: number };
export type SnapState = "collapsed" | "expanded";

/** Share of the window the tall sheet takes; the rest (top ~25%) stays map. */
export const EXPANDED_SHARE = 0.75;

/** Finger speed (px/s) from which the direction of the fling decides, not the position. */
export const FLING_VELOCITY = 600;

/** The tall state never ends up shorter than the content it replaces. */
export function sheetHeights(collapsed: number, expanded: number): SheetHeights {
  "worklet";
  return { collapsed, expanded: Math.max(collapsed, expanded) };
}

/** Height of the sheet at `progress` (0 = collapsed, 1 = expanded). */
export function heightAt(progress: number, h: SheetHeights): number {
  "worklet";
  return h.collapsed + (h.expanded - h.collapsed) * progress;
}

/** Progress for a height; 0 when the two states coincide (nothing to expand into). */
export function progressFor(height: number, h: SheetHeights): number {
  "worklet";
  const span = h.expanded - h.collapsed;
  if (span <= 0) return 0;
  return Math.min(1, Math.max(0, (height - h.collapsed) / span));
}

/**
 * Progress after the finger moved `translationY` px (positive = down, as the gesture reports it)
 * from a drag that began at `startProgress`. Clamped: the sheet does not stretch past either state.
 */
export function dragProgress(startProgress: number, translationY: number, h: SheetHeights): number {
  "worklet";
  return progressFor(heightAt(startProgress, h) - translationY, h);
}

/**
 * Where a released sheet goes. A fling (|velocityY| >= FLING_VELOCITY) is obeyed: finger moving
 * up expands, down collapses. Slower releases snap to the nearer of the two heights.
 */
export function snapTarget(height: number, velocityY: number, h: SheetHeights): SnapState {
  "worklet";
  if (h.expanded <= h.collapsed) return "collapsed";
  if (velocityY <= -FLING_VELOCITY) return "expanded";
  if (velocityY >= FLING_VELOCITY) return "collapsed";
  const mid = (h.collapsed + h.expanded) / 2;
  return height >= mid ? "expanded" : "collapsed";
}
