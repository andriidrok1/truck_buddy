import { useEffect, useLayoutEffect, useRef } from "react";
import { BackHandler, Platform } from "react-native";

import { useTripStore } from "../state/tripStore";
import type { Phase } from "../types";
import { backAction } from "../utils/backAction";

const trip = useTripStore.getState;

export type BackLayers = {
  /** The map screen is the one on top. Under the truck profile, the history or the tester screen the press is
   *  theirs: this listener stays registered while they are open, and Back on "Your truck" ended the route in
   *  preview (and did nothing at all while driving) instead of closing the screen (emulator, 2026-11-11). */
  focused: boolean;
  state: { micCard: boolean; routesOpen: boolean; reply: boolean; phase: Phase };
  cancel: () => void;
  closeOptions: () => void;
  endRoute: () => void;
};

/** Moved verbatim from app/index.tsx; `layers` is this render's state and handlers, mirrored at commit. */
export function useHardwareBack(
  layers: BackLayers,
  setMicCard: (card: null) => void,
  setRoutesOpen: (open: boolean) => void,
  setCaption: (caption: null) => void,
) {
  // Hardware Back closes the top layer (src/utils/backAction.ts); only with nothing of ours open does
  // it leave the app. While driving it does nothing: one stray press must not drop the route.
  // The screen's own layers and handlers are mirrored at commit (like phaseRef above), the route and the
  // driving flag are read from the trip store, so the one listener registered on mount never acts on a
  // stale closure.
  const backRefs = useRef<BackLayers>({
    focused: true,
    state: { micCard: false, routesOpen: false, reply: false, phase: "ready" as Phase },
    cancel: () => {},
    closeOptions: () => {},
    endRoute: () => {},
  });
  useLayoutEffect(() => {
    backRefs.current = layers;
  });
  useEffect(() => {
    if (Platform.OS !== "android") return;
    const sub = BackHandler.addEventListener("hardwareBackPress", () => {
      const { focused, state, cancel, closeOptions, endRoute } = backRefs.current;
      if (!focused) return false; // another screen is on top: the navigator closes it
      const t = trip();
      switch (backAction({ ...state, hasRoute: !!t.active, driving: t.driving })) {
        case "closeMicCard":
          setMicCard(null);
          return true;
        case "closeRoutes":
          setRoutesOpen(false);
          return true;
        case "closeReply":
          setCaption(null);
          return true;
        case "cancelTurn":
          cancel();
          return true;
        case "closeOptions":
          closeOptions();
          return true;
        case "endRoute":
          endRoute();
          return true;
        case "ignore":
          return true;
        default:
          return false;
      }
    });
    return () => sub.remove();
  }, [setMicCard, setRoutesOpen, setCaption]); // state setters, stable: registered once
}
