// What the hardware Back button does on the map screen, top layer first. Pure, so the table is
// testable: Android drivers press Back to close whatever is on top, and until now the app simply
// left to the launcher (QA 2026-10-29, B-UI-1).
import type { Phase } from "../types";

export type BackState = {
  micCard: boolean; // the microphone permission card is up
  routesOpen: boolean; // the "Routes (N)" alternatives sheet is up
  phase: Phase;
  hasRoute: boolean; // a route is set (preview or driving)
  driving: boolean; // navigation started ("Go" pressed)
};

export type BackAction =
  | "closeMicCard"
  | "closeRoutes"
  | "cancelTurn" // listening / thinking: drop the turn, back to idle
  | "closeOptions" // the options or providers list: same as its Close button
  | "endRoute" // route preview: same as its End route button
  | "ignore" // while driving: Back must not throw the driver out of the route
  | null; // nothing of ours is open: let the system handle it (leave the app)

export function backAction(s: BackState): BackAction {
  if (s.micCard) return "closeMicCard";
  if (s.routesOpen) return "closeRoutes";
  if (s.phase === "listening" || s.phase === "thinking") return "cancelTurn";
  if (s.phase === "options" || s.phase === "providers") return "closeOptions";
  if (s.driving) return "ignore";
  if (s.hasRoute) return "endRoute";
  return null;
}
