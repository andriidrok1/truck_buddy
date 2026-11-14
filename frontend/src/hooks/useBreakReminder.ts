import { useEffect, useRef } from "react";

import { useTripStore } from "../state/tripStore";
import type { ActiveRoute } from "../types";
import { PRIORITY } from "../voice/queue";
import type { SayOptions } from "./useSpeechQueue";
import type { TruckProfile } from "./useTruckProfile";

const trip = useTripStore.getState;

/**
 * Whether "you've been driving for N hours" is due: the time counts from Go (`drivingSince`, null when not
 * driving) or from the last reminder, whichever is later. It used to count from when the route was created, so
 * a route left in preview overnight said "you've been driving for 8 hours" in the morning.
 */
export function breakDue(drivingSince: number | null, lastReminded: number, now: number, limitMs: number): boolean {
  if (drivingSince == null) return false;
  return now - Math.max(drivingSince, lastReminded) >= limitMs;
}

/** Break reminder while driving a route. */
export function useBreakReminder(active: ActiveRoute | null, truck: TruckProfile, say: (text: string, opts: SayOptions) => void) {
  const lastReminded = useRef(0); // outlives a reroute (a new `active` restarts the timer below)
  useEffect(() => {
    if (!active || !truck.break_hours) return;
    const limitMs = truck.break_hours * 3600 * 1000;
    const t = setInterval(() => {
      const now = Date.now();
      if (!breakDue(trip().drivingSince, lastReminded.current, now, limitMs)) return;
      lastReminded.current = now; // remind again after the next interval
      const who = truck.driver_name ? `Hey ${truck.driver_name}, ` : "";
      const hrs = truck.break_hours === 1 ? "an hour" : `${truck.break_hours} hours`;
      const text = `${who}you've been driving for ${hrs}. Want me to find a rest area?`;
      say(text, { priority: PRIORITY.WEATHER, kind: "ambient", key: "break", ttlMs: 10 * 60 * 1000 });
    }, 60000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, truck.break_hours, truck.driver_name]);
}
