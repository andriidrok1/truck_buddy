// Geometry of the draggable copilot sheet. Three resting heights, all derived from the content:
//   peek  - grabber, the first header line and the controls row; the rest hidden (see the map)
//   auto  - the content's natural height, where every phase starts
//   full  - only when the body is clipped in auto: the whole content, capped at FULL_SHARE of the
//           window, with the body scrolling. Never empty space.
// Pure functions so the snap decision can be tested without a gesture; every one is a worklet
// because the gesture callbacks call them on the UI thread.

export type SnapState = "peek" | "auto" | "full";
export type SheetHeights = { peek: number; auto: number; full?: number };

/** Measurements the sheet takes with onLayout while resting in auto. */
export type SheetMeasure = {
  /** Height of everything but the hidden part of the body: grabber + first header line + controls. */
  peek: number;
  /** The sheet's natural height. */
  auto: number;
  /** Height the sheet would need to show the whole body unclipped. */
  content: number;
  window: number;
};

/** Cap of the full state, so the map keeps its top strip. */
export const FULL_SHARE = 0.85;
/** Finger speed (px/s) from which the direction of the fling decides, not the position. */
export const FLING_VELOCITY = 600;
/** How far the sheet stretches past its last state before it springs back. */
export const RUBBER_MAX = 24;

/** The resting heights. `full` exists only when auto clips the body. */
export function sheetHeights(m: SheetMeasure): SheetHeights {
  "worklet";
  const auto = Math.max(0, m.auto);
  const peek = Math.min(auto, Math.max(0, m.peek));
  const h: SheetHeights = { peek, auto };
  if (m.content > auto + 1) h.full = Math.max(auto, Math.min(m.content, m.window * FULL_SHARE));
  return h;
}

/** States the sheet can rest in, shortest first. */
export function availableStates(h: SheetHeights): SnapState[] {
  "worklet";
  const states: SnapState[] = h.peek < h.auto ? ["peek", "auto"] : ["auto"];
  if (h.full !== undefined && h.full > h.auto) states.push("full");
  return states;
}

/** Height of a state; a missing full state is the auto height. */
export function stateHeight(s: SnapState, h: SheetHeights): number {
  "worklet";
  if (s === "peek") return h.peek;
  if (s === "full") return h.full ?? h.auto;
  return h.auto;
}

/** Overshoot `o` px past an end, softened: asymptotic to RUBBER_MAX. */
export function rubberBand(o: number): number {
  "worklet";
  return (RUBBER_MAX * o) / (o + RUBBER_MAX);
}

/**
 * Sheet height after the finger moved `translationY` px (positive = down, as the gesture reports
 * it) from a drag that began at `startHeight`. Past the shortest or tallest state it stretches
 * by a rubber band of at most RUBBER_MAX px.
 */
export function dragHeight(startHeight: number, translationY: number, h: SheetHeights): number {
  "worklet";
  const min = h.peek;
  const max = h.full ?? h.auto;
  const raw = startHeight - translationY;
  if (raw > max) return max + rubberBand(raw - max);
  if (raw < min) return min - rubberBand(min - raw);
  return raw;
}

/**
 * Where a released sheet goes. A fling (|velocityY| >= FLING_VELOCITY) moves one state from
 * `from` in its direction (up = taller); slower releases snap to the nearest available height.
 */
export function snapTarget(height: number, velocityY: number, h: SheetHeights, from: SnapState): SnapState {
  "worklet";
  const states = availableStates(h);
  const at = Math.max(0, states.indexOf(from));
  if (velocityY <= -FLING_VELOCITY) return states[Math.min(states.length - 1, at + 1)];
  if (velocityY >= FLING_VELOCITY) return states[Math.max(0, at - 1)];
  let best = states[0];
  let bestD = Infinity;
  for (let i = 0; i < states.length; i++) {
    const d = Math.abs(stateHeight(states[i], h) - height);
    if (d < bestD) {
      bestD = d;
      best = states[i];
    }
  }
  return best;
}
