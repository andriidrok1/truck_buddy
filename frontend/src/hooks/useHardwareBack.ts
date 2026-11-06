import { useEffect, useLayoutEffect, useRef } from "react";
import { BackHandler, Platform } from "react-native";

import { useTripStore } from "../state/tripStore";
import type { Phase } from "../types";
import { backAction } from "../utils/backAction";

const trip = useTripStore.getState;

export type BackLayers = {
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
      const { state, cancel, closeOptions, endRoute } = backRefs.current;
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
